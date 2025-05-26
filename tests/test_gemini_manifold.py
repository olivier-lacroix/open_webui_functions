from typing import cast
from aiocache.base import BaseCache
import pytest
import pytest_asyncio
from unittest.mock import (
    patch,
    MagicMock,
    AsyncMock,
    call,
)
import sys

# --- Mock problematic Open WebUI modules BEFORE they are imported by your plugin ---
mock_chats_module = MagicMock()
mock_files_module = MagicMock()
mock_functions_module = MagicMock()
mock_storage_module = MagicMock()
mock_utils_module = MagicMock()

mock_chats_module.Chats = MagicMock()
mock_files_module.FileForm = MagicMock()
mock_files_module.Files = MagicMock()
mock_functions_module.Functions = MagicMock()
mock_storage_module.Storage = MagicMock()

sys.modules["open_webui.models.chats"] = mock_chats_module
sys.modules["open_webui.models.files"] = mock_files_module
sys.modules["open_webui.models.functions"] = mock_functions_module
sys.modules["open_webui.storage.provider"] = mock_storage_module
sys.modules["open_webui.utils.misc"] = mock_utils_module

# --- Now, the import of your plugin should use the mocks ---
from plugins.pipes.gemini_manifold import (
    Pipe,
    types as gemini_types,
)  # gemini_types is google.genai.types


@pytest.fixture
def mock_pipe_valves_data():
    return {
        "GEMINI_API_KEY": "test_default_api_key_from_valves",
        "REQUIRE_USER_API_KEY": False,
        "GEMINI_API_BASE_URL": "https://test.googleapis.com",
        "MODEL_WHITELIST": "*",
        "MODEL_BLACKLIST": None,
        "CACHE_MODELS": True,
        "THINKING_BUDGET": 8192,
        "USE_FILES_API": True,
        "THINKING_MODEL_PATTERN": r"thinking|gemini-2.5",
        "EMIT_INTERVAL": 1,
        "EMIT_STATUS_UPDATES": False,
        "LOG_LEVEL": "INFO",
    }


# Helper to setup Pipe instance for tests that need it
# This reduces boilerplate in each test function.
@pytest_asyncio.fixture
async def pipe_instance_fixture(mock_pipe_valves_data):
    mock_gemini_client_instance = MagicMock()

    with (
        patch(
            "plugins.pipes.gemini_manifold.genai.Client",
            return_value=mock_gemini_client_instance,
        ) as MockedGenAIClientConstructor,
        patch.object(
            Pipe, "_add_log_handler", MagicMock()
        ) as mock_internal_add_log_handler,
        patch(
            "sys.stdout",
            MagicMock(),  # Suppress print/log output during setup
        ),
    ):
        pipe = Pipe()
        pipe.valves = Pipe.Valves(**mock_pipe_valves_data)
        yield pipe  # Use yield to make it a fixture that provides the instance

    # Teardown: Clear caches
    Pipe._get_or_create_genai_client.cache_clear()
    cache_instance = getattr(pipe._get_genai_models, "cache")
    if cache_instance:
        await cast(BaseCache, cache_instance).clear()


def test_pipe_initialization_with_api_key(mock_pipe_valves_data):
    mock_gemini_client_instance = MagicMock()

    with (
        patch(
            "plugins.pipes.gemini_manifold.genai.Client",
            return_value=mock_gemini_client_instance,
        ) as MockedGenAIClientConstructor,
        patch.object(
            Pipe, "_add_log_handler", MagicMock()
        ) as mock_internal_add_log_handler,
        patch("sys.stdout", MagicMock()),
    ):
        try:
            pipe_instance = Pipe()
            pipe_instance.valves = Pipe.Valves(**mock_pipe_valves_data)  # Added

            assert isinstance(pipe_instance.valves, Pipe.Valves)
            assert (
                pipe_instance.valves.GEMINI_API_KEY
                == "test_default_api_key_from_valves"
            )

            # Trigger client creation
            pipe_instance._get_user_client(
                pipe_instance.valves, "test_user_email@example.com"
            )

            MockedGenAIClientConstructor.assert_called_once_with(
                api_key="test_default_api_key_from_valves",
                http_options=gemini_types.HttpOptions(
                    base_url="https://test.googleapis.com"
                ),
            )
        finally:
            Pipe._get_or_create_genai_client.cache_clear()


@pytest.mark.asyncio
async def test_genai_contents_from_messages_simple_user_text(pipe_instance_fixture):
    pipe_instance = pipe_instance_fixture  # Get the pre-configured pipe instance

    messages_body = [{"role": "user", "content": "Hello!"}]
    messages_db = None
    upload_documents = True
    mock_event_emitter = AsyncMock()

    with patch(
        "plugins.pipes.gemini_manifold.types.Part.from_text"
    ) as mock_part_from_text:
        mock_part_from_text.return_value = MagicMock(spec=gemini_types.Part)

        contents = await pipe_instance._genai_contents_from_messages(
            messages_body,
            messages_db,
            upload_documents,
            mock_event_emitter,
        )

        mock_part_from_text.assert_called_once_with(text="Hello!")
        assert len(contents) == 1
        content_item = contents[0]
        assert content_item.role == "user"
        assert len(content_item.parts) == 1
        assert content_item.parts[0] == mock_part_from_text.return_value
        mock_event_emitter.assert_not_called()


@pytest.mark.asyncio
async def test_genai_contents_from_messages_youtube_link_mixed_with_text(
    pipe_instance_fixture,
):
    pipe_instance = pipe_instance_fixture  # Get the pre-configured pipe instance

    # Arrange: Inputs
    youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    text_before_raw = "Look at this: "  # Has trailing space
    text_after_raw = " it's great!"  # Has leading space

    # Expected stripped text for Part.from_text calls
    text_before_stripped = text_before_raw.strip()
    text_after_stripped = text_after_raw.strip()

    user_content_string = f"{text_before_raw}{youtube_url}{text_after_raw}"
    messages_body = [{"role": "user", "content": user_content_string}]
    messages_db = None
    upload_documents = (
        True  # Does not affect this specific test path but set for consistency
    )
    mock_event_emitter = AsyncMock()

    # Mock types.Part.from_text to control its return values for distinct text segments
    mock_text_part_before_obj = MagicMock(spec=gemini_types.Part, name="TextPartBefore")
    mock_text_part_after_obj = MagicMock(spec=gemini_types.Part, name="TextPartAfter")

    # This patch targets 'types.Part.from_text' as used within the gemini_manifold module.
    with patch(
        "plugins.pipes.gemini_manifold.types.Part.from_text"
    ) as mock_part_from_text:
        # Configure side_effect to return specific mocks for expected text segments.
        # This helps verify that from_text is called with the correct, segmented text.
        def from_text_side_effect(text):
            if text == text_before_stripped:
                return mock_text_part_before_obj
            elif text == text_after_stripped:
                return mock_text_part_after_obj
            # If called with unexpected text (e.g., the full string containing the URL),
            # return a generic mock or raise an error to make test failure clearer.
            # For this test, we expect specific calls. If other calls happen, assertions on
            # call_args_list or part content will catch it.
            # Let's return a generic mock for unexpected calls to avoid crashing the test prematurely
            # and allow other assertions to pinpoint the issue.
            generic_mock_part = MagicMock(
                spec=gemini_types.Part, name=f"GenericTextPart_{text[:10]}"
            )
            generic_mock_part.text = text  # Store text for easier debugging if needed
            return generic_mock_part

        mock_part_from_text.side_effect = from_text_side_effect

        # Act: Call the method under test
        contents = await pipe_instance._genai_contents_from_messages(
            messages_body,
            messages_db,
            upload_documents,
            mock_event_emitter,
        )

        # Assert: Verify the outcome
        assert len(contents) == 1, "Should produce one content item"

        content_item = contents[0]
        assert content_item.role == "user", "Content item role should be 'user'"

        # --- Assertions for correct part segmentation and ordering (THESE WILL LIKELY FAIL with current code) ---
        # This is the core of the test, expecting proper interleaving.
        # Current code likely produces 2 parts: [YouTubePart, FullTextPart(including URL)]

        assert len(content_item.parts) == 3, (
            f"Expected 3 parts (text, youtube, text), but got {len(content_item.parts)}. Parts: {[str(p) for p in content_item.parts]}"
        )

        # Verify calls to types.Part.from_text
        # Expected: two calls, one for text_before_stripped, one for text_after_stripped.
        # Current code likely calls it once with the full user_content_string.
        expected_from_text_calls = [
            call(text=text_before_stripped),
            call(text=text_after_stripped),
        ]
        # Using assert_has_calls because the order of these text parts relative to each other
        # (if there were multiple text segments from markdown processing) might not be fixed,
        # but for this simple case, call_args_list would be stricter.
        # However, given the side_effect, checking the objects in content_item.parts is more direct.
        # Let's check call_count first, then the actual parts.
        assert mock_part_from_text.call_count == 2, (
            f"Expected Part.from_text to be called 2 times, but was called {mock_part_from_text.call_count} times. Calls: {mock_part_from_text.call_args_list}"
        )

        # Assert the content and order of parts
        # Part 1: Text before the YouTube link
        assert content_item.parts[0] is mock_text_part_before_obj, (
            f"Part 0 was not the expected 'text_before' mock. Got: {content_item.parts[0]}"
        )

        # Part 2: The YouTube link
        youtube_part = content_item.parts[1]
        assert isinstance(youtube_part, gemini_types.Part), (
            f"Part 1 is not a gemini_types.Part. Got: {type(youtube_part)}"
        )
        assert hasattr(youtube_part, "file_data"), (
            "YouTube part (Part 1) should have 'file_data' attribute"
        )
        assert youtube_part.file_data is not None, (
            "YouTube part's file_data should not be None"
        )
        assert youtube_part.file_data.file_uri == youtube_url, (
            f"YouTube part URI mismatch. Expected: {youtube_url}, Got: {youtube_part.file_data.file_uri}"
        )
        # Ensure it's not accidentally a text part
        assert not hasattr(youtube_part, "text") or youtube_part.text is None, (
            "YouTube part should not have a 'text' attribute or it should be None"
        )

        # Part 3: Text after the YouTube link
        assert content_item.parts[2] is mock_text_part_after_obj, (
            f"Part 2 was not the expected 'text_after' mock. Got: {content_item.parts[2]}"
        )

        # Ensure event_emitter was not called for this simple case
        mock_event_emitter.assert_not_called()


@pytest.mark.asyncio
async def test_genai_contents_from_messages_user_text_with_pdf(pipe_instance_fixture):
    pipe_instance = pipe_instance_fixture

    # Arrange: Inputs
    user_text_content = "Please analyze this PDF."
    pdf_file_id = "test-pdf-id-001"
    pdf_file_name = "mydoc.pdf"
    fake_pdf_bytes = b"%PDF-1.4 fake content..."
    pdf_mime_type = "application/pdf"

    messages_body = [{"role": "user", "content": user_text_content}]

    # Construct messages_db with file attachment info
    messages_db = [
        {
            "id": "user-msg-db-id-1",
            "parentId": None,
            "childrenIds": [],
            "role": "user",
            "content": user_text_content,  # Should match messages_body content
            "timestamp": 1620000000,
            "files": [
                {
                    "id": "attachment-id-pdf-1",  # ID of the attachment link
                    "type": "file",
                    "file": {  # FileInfoTD
                        "id": pdf_file_id,
                        "name": pdf_file_name,
                        "size": len(fake_pdf_bytes),
                        "timestamp": 1620000000,
                    },
                    "references": [],
                    "tool_code_id": None,
                }
            ],
        }
    ]
    upload_documents = True
    mock_event_emitter = AsyncMock()

    # Mock objects for parts
    mock_pdf_part_obj = MagicMock(spec=gemini_types.Part, name="PdfPart")
    mock_text_part_obj = MagicMock(spec=gemini_types.Part, name="TextPart")

    # Patch _get_file_data, Part.from_bytes, and Part.from_text
    with (
        patch.object(
            Pipe, "_get_file_data", return_value=(fake_pdf_bytes, pdf_mime_type)
        ) as mock_get_file_data,
        patch(
            "plugins.pipes.gemini_manifold.types.Part.from_bytes",
            return_value=mock_pdf_part_obj,
        ) as mock_part_from_bytes,
        patch(
            "plugins.pipes.gemini_manifold.types.Part.from_text",
            return_value=mock_text_part_obj,
        ) as mock_part_from_text,
    ):
        # Act
        contents = await pipe_instance._genai_contents_from_messages(
            messages_body,
            messages_db,
            upload_documents,
            mock_event_emitter,
        )

        # Assert
        mock_get_file_data.assert_called_once_with(pdf_file_id)
        mock_part_from_bytes.assert_called_once_with(
            data=fake_pdf_bytes, mime_type=pdf_mime_type
        )
        mock_part_from_text.assert_called_once_with(text=user_text_content)

        assert len(contents) == 1
        user_content_obj = contents[0]
        assert user_content_obj.role == "user"
        assert len(user_content_obj.parts) == 2

        # Check order: PDF part should be first, then text part
        assert user_content_obj.parts[0] is mock_pdf_part_obj, (
            "First part should be the PDF mock."
        )
        assert user_content_obj.parts[1] is mock_text_part_obj, (
            "Second part should be the text mock."
        )

        mock_event_emitter.assert_not_called()


def teardown_module(module):
    """Cleans up sys.modules after tests in this file are done."""
    del sys.modules["open_webui.models.chats"]
    del sys.modules["open_webui.models.files"]
    del sys.modules["open_webui.models.functions"]
    del sys.modules["open_webui.storage.provider"]
    del sys.modules["open_webui.utils.misc"]

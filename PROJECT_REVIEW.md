# Project Review: cast2md (v2)

## 1. Executive Summary

**Overall Status**: 🟢 **Production Ready**
The project has made significant progress since the last review. The critical infinite retry bug is fixed, basic testing infrastructure is in place, and substantial new functionality (Search, Transcript Exports) has been added.

| Category | Status | Rating | Trend |
|----------|--------|--------|-------|
| **Architecture** | Excellent | ⭐⭐⭐⭐⭐ | ➡️ |
| **User Interface** | Great | ⭐⭐⭐⭐ | ➡️ |
| **Features** | Excellent | ⭐⭐⭐⭐⭐ | ⬆️ (New Search & Exports) |
| **Reliability** | Good | ⭐⭐⭐⭐ | ⬆️ (Retry logic fixed) |
| **Testing** | Basic | ⭐⭐ | ⬆️ (Started) |

## 2. New Functionality Review

### 🔍 Full-Text Search
- **Implementation**: Uses SQLite's FTS5 extension effectively.
- **Features**: Supports boolean queries (`python AND async`), filtering by feed, and searching within specific episodes.
- **Code Quality**: `src/cast2md/api/search.py` is clean and uses Pydantic models well.
- **Gap**: No automated tests found for search logic in `tests/`.

### 📄 Transcript Exports
- **Formats**: Markdown, Text, SRT, VTT, JSON.
- **Implementation**: `src/cast2md/export/formats.py` handles parsing and conversion robustly.
- **Verification**: Verified via new unit tests (`tests/test_export_formats.py`). The parsing logic correctly handles timestamps and metadata.

### 🧠 Transcript Workflow Logic
- **Rating**: ⭐⭐⭐⭐⭐ (Excellent)
- **Assessment**: The logic to prioritize external transcript downloads (via Podcast 2.0 or Pocket Casts) before attempting audio download/transcription is highly efficient.
- **Impact**: This "Download First, Generate Later" approach significantly reduces:
    - **Bandwidth**: Large audio files are not downloaded if a text transcript is available.
    - **Compute**: Expensive Whisper inference (GPU/CPU) is completely bypassed for episodes with existing transcripts.
- **Implementation**: The separation of `JobType.TRANSCRIPT_DOWNLOAD` from `JobType.DOWNLOAD` allows for granular control and optimization.

### 🤖 MCP Server (Agent Integration)
- **Status**: Implemented (`src/cast2md/mcp/`)
- **Capabilities**: Exposes full search, queue management, and feed operations to external agents (like Claude Desktop).
- **Tools**: `search_transcripts`, `search_episodes`, `queue_episode`, `add_feed`, `refresh_feed`.
- **Resources**: `cast2md://feeds`, `cast2md://episodes/{id}`, `cast2md://episodes/{id}/transcript`.
- **Verdict**: A major value-add. It transforms the headless server into an "Agent-Ready" platform, allowing LLMs to interact with the podcast library directly.

## 3. Resolved Issues

### ✅ Fixed: Infinite Retry Loop
The "19/3 attempts" poison pill bug is resolved. `JobRepository.reclaim_stale_jobs` now correctly fails jobs that exceed max attempts. Regression tests added.

### ✅ Improved: Testing
Authentication and export logic are now covered. `tests/` directory is no longer empty.

## 4. Remaining Risks & Recommendations

1.  **Search Testing**: Add integration tests for `TranscriptSearchRepository`.
2.  **Rate Limiting**: Address the `429 Too Many Requests` issue for heavy users/bots.
3.  **Feature Recommendations**:
    - **OPML Import/Export**: Critical for user migration. Use `list_feeds` as a base.
    - **Speaker Diarization**: The current "wall of text" is hard to read. Whisper supports diarization; exposing this would be huge.
    - **Native Summarization**: While the MCP server allows *external* agents to summarize, a *native* pipeline (using local Ollama or OpenAI) to generate and store summaries in the DB would make the UI much richer without requiring a desktop agent.
    - **MCP Efficiency**: Add `get_recent_episodes(limit, days)` tool. Currently, agents must poll every feed individually to find "what's new", which is inefficient.
    - **Vector Search**: Semantic search is recommended over keyword search. See newly added documentation:
        - `docs/vector_embeddings_concept.md` (Concepts)
        - `docs/python_vector_solutions.md` (Implementation Plan)
    - **GUI/UX**:
        - **Dark Mode**: Currently forced to light mode. Enable auto-detection or a toggle.
        - **Mobile**: Nav bar needs responsive treatment (hamburger menu or scrollable container).
        - See `docs/gui_recommendations.md` for specific CSS changes.

## 5. Conclusion
The project is in excellent shape. The new transcript features are well-integrated, stability issues are fixed, and the MCP server opens up a world of Agentic possibilities.

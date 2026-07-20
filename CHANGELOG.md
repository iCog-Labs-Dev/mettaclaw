# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [prod-2026-07-17] — 2026-07-17

### Added
- **Telegram image support** — photos are downloaded, sanitized (re-encoded via Pillow to strip EXIF/metadata and LSB steganographic payloads), and queued for agent processing
- **Telegram PDF support** — PDF documents are downloaded, text-extracted via `pypdf`, and supplied directly to the agent as document context (content limited to 20,000 characters)
- **Telegram voice/audio transcription** — voice notes and audio files are transcribed to text via OpenRouter Whisper Large V3
- **Image generation skill** — agent can generate images via configurable provider (defaults to OpenRouter; configure with `IMAGE_PROVIDER`/`IMAGE_MODEL`) and deliver them to the active Telegram conversation
- **`describe-image` vision fallback skill** — non-vision providers can inspect attached images through a dedicated vision model (OpenRouterVision); memoized per turn to avoid redundant re-description
- **`telegramify-markdown` integration** — safe MarkdownV2 formatting for outbound Telegram messages, preventing parse errors from unescaped characters
- **Media capabilities in `telegram_profile.yaml`** — new settings: `allow_files`, `allow_media`, `allow_audio`, `allow_image_generation`, `text_only` (now `false`), and `image_generation` callable capability with ethics constraints
- **System/user prompt separation** — MeTTa sends prompts delimited by `:-:-:-:`; `lib_llm_ext` splits them so providers receive a real system/developer prompt instead of flattening everything into one user message
- **`pending_media_count()` and `get_pending_context_block()`** — new MeTTa-callable functions in `lib_llm_ext` for detecting pending media and injecting document context into the agent turn
- **Media/document context auto-clear** — out-of-band media and document context are cleared after the agent's first response to prevent stale context from leaking into later turns
- **Production CI credentials** — `GHCR_USER` and `GHCR_TOKEN` env vars passed to deploy step, enabling authentication for private GHCR image pulls

### Changed
- **Web search modernized** — replaced legacy DuckDuckGo HTML scraper (`channels/websearch.py`) with the maintained `ddgs` package; search results now include title, URL, and snippet in every result tuple
- **`callProvider` signature** — accepts new `reasoning` and `media` parameters, dispatches media to vision-capable providers directly or injects a describe-image hint for non-vision providers
- **Telegram prompts updated** — revised to prevent proactive sends and improve response behavior
- **`telegram_profile.yaml` restructured** — previously hard-coded allowed Telegram chat IDs removed (review before deployment); media-related settings added; `OpenRouter` added as `image_provider` default; `VISION_MODEL` and `LLM_MODEL` configurable
- **OpenRouter Whisper** — switched from OpenAI Whisper to OpenRouter for audio transcription (requires `OPENROUTER_API_KEY`)
- **Unsupported media message** — changed from _"I can only process text messages here"_ to a clearer capability notice listing supported types (text, images, PDF files, voice/audio)
- **Provider model default** — OpenAI provider model updated from `gpt-5.4` to `gpt-5.5`
- **`lib_llm_ext.py` refactored** — added `OpenAIProvider` class, `_split_system_user()`, `_build_user_content_with_media()`, `_clean_text()`, stable cache key generation, and lazy media_handler imports for text-only deployments

### Fixed
- **Non-vision image pipeline** — `describe-image` now works end-to-end: non-vision providers receive a hint to use the skill, the skill calls the vision provider, and the caption is returned to the agent
- **`gpt-image-1` response_format parameter** — dropped `response_format` for `gpt-image-1` (was causing 400 error)
- **OpenAI image routing** — image turns now correctly routed through `callProvider` vision path instead of bypassing it
- **PDF/image context injection** — document context and image blocks are injected into both the `callProvider` path and the `$send` path, ensuring consistent context regardless of provider
- **Typing indicator timeout** — hard 120-second timeout added to prevent infinite typing indicator if the agent gets stuck
- **Docker CI deploy order** — `docker login` to ghcr now happens before `docker pull`, fixing production deployment of private images
- **Describe-image pointer scope** — only shown for genuinely non-vision providers (vision-capable providers like OpenAI/Anthropic receive images directly)
- **Outdated websearch code** — legacy `channels/websearch.py` removed; routing updated in `lib_omegaclaw.metta`, `src/channels.metta`, and `src/skills.metta`
- **`telegramify-markdown` dependency** — added to the Docker Python dependencies for correct outbound formatting in containerized deployments
- **Prompt newline in `telegram_profile.yaml`** — trailing newline issue in prompt configuration fixed

### Security
- **Media ethics middleware** — images are sanitized and re-encoded before processing to remove metadata; incoming document text and audio transcripts are checked against the `is_category_blocked` ethics pass before reaching the agent
- **Media rejection scoped** — media rejection notice is now limited to direct tag or reply to the bot (not broadcast to every unsolicited media message in a group)

### Configuration notes
- Audio transcription requires `OPENROUTER_API_KEY` (set via environment or Infisical)
- Vision fallback requires `OPENROUTER_API_KEY` (routed through the `OpenRouterVision` provider; model set via `VISION_MODEL`) unless the selected chat provider supports images directly
- Image generation defaults to OpenRouter; configure with `IMAGE_PROVIDER` and `IMAGE_MODEL`
- Review `memory/telegram_profile.yaml` before deployment — previously hard-coded allowed chat IDs have been removed and must be reconfigured for your environment

[prod-2026-07-17]: https://github.com/iCog-Labs-Dev/mettaclaw/releases/tag/prod-2026-07-17

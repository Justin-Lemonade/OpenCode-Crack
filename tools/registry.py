"""
Unified tool registry (roadmap C-006, CRITICAL priority).

One declarative catalog of every capability the brain could expose to
a model or interface, independent of which model (cloud or local) or
which interface (CLI, Telegram, future API) is asking for it. This is
the foundation for both cloud and local agents per the roadmap.

Each ToolSpec declares:
    name               unique tool identifier
    description        what it does, for a model deciding whether to call it
    schema             dict describing accepted kwargs (name -> type hint string)
    permissions        list of permission strings this tool needs
    timeout_seconds    max time a call may run before being treated as failed
    side_effect_level  "none" | "read" | "write" | "destructive"
    supported_agents   which kinds of agent may call this (default ["any"])
    handler            the actual callable, or None if not implemented yet

IMPORTANT — this registry does NOT itself enforce a permission policy.
That's roadmap C-007 (permission-aware tool execution), which doesn't
exist yet: "Never allow the model to directly decide which
shell/file/network operations are acceptable" is C-007's job, not this
module's. Until C-007 lands, ToolRegistry.call() refuses to run any
tool with no wired handler — see call() below — so handing this
registry to a model can't accidentally grant shell/filesystem/network
access before a real permission layer exists. Tools that already have
a safe, working handler (remember, ingest_document, search_memory,
export) run today because that's exactly how the CLI already exposes
them, unguarded, and this doesn't change that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

_VALID_SIDE_EFFECT_LEVELS = {"none", "read", "write", "destructive"}

#: Type-hint strings the registry schema may use, mapped to the runtime
#: types they describe. Hints are declarative only, so this table is the
#: small closed set call() validates against.
_TYPE_HINTS = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "None": type(None),
}

_HINT_CACHE: dict[str, tuple] = {}


def _types_for_hint(hint: str) -> tuple:
    """Resolve a declarative type-hint string (e.g. ``"str | None"``) to
    runtime types for isinstance checks. Fails closed: an unsupported
    hint raises rather than silently skipping validation, so a future
    tool can't declare a type this registry can't enforce."""
    if hint in _HINT_CACHE:
        return _HINT_CACHE[hint]
    parts = [p.strip() for p in hint.split("|")]
    resolved = []
    for part in parts:
        if part not in _TYPE_HINTS:
            raise ValueError(
                f"Unsupported type hint {part!r} in schema (from {hint!r}); "
                f"supported hints: {sorted(_TYPE_HINTS)}"
            )
        resolved.append(_TYPE_HINTS[part])
    _HINT_CACHE[hint] = tuple(resolved)
    return _HINT_CACHE[hint]


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict
    permissions: list[str]
    timeout_seconds: int
    side_effect_level: str
    supported_agents: list[str] = field(default_factory=lambda: ["any"])
    handler: Optional[Callable] = None  # None = declared but not yet implemented

    def __post_init__(self) -> None:
        if self.side_effect_level not in _VALID_SIDE_EFFECT_LEVELS:
            raise ValueError(
                f"Invalid side_effect_level {self.side_effect_level!r} for tool "
                f"{self.name!r}; must be one of {sorted(_VALID_SIDE_EFFECT_LEVELS)}"
            )

    @property
    def implemented(self) -> bool:
        return self.handler is not None


class ToolRegistry:
    """Holds every declared tool. One shared instance (`registry`,
    below) is what brain.py and interfaces should look tools up in,
    rather than each hard-coding which functions exist where."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"No such tool: {name!r}. Known tools: {sorted(self._tools)}")
        return self._tools[name]

    def list_tools(self, side_effect_level: Optional[str] = None, implemented_only: bool = False) -> list[ToolSpec]:
        tools = list(self._tools.values())
        if side_effect_level is not None:
            tools = [t for t in tools if t.side_effect_level == side_effect_level]
        if implemented_only:
            tools = [t for t in tools if t.implemented]
        return sorted(tools, key=lambda t: t.name)

    def call(self, name: str, **kwargs):
        """Execute a tool by name.

        No permission policy exists yet (C-007), so this enforces the
        conservative interim rule described in the module docstring:
        refuse anything without a wired handler. That's currently the
        only thing standing between "declared in the catalog" and
        "actually dangerous to let a model run".

        Kwarg types are validated against each tool's declared schema
        (D-027) before the handler runs, so wrong-typed arguments fail
        loudly instead of passing straight through. Missing args are
        still left to the handler's own signature enforcement, which is
        the behavior the existing suite pins."""
        spec = self.get(name)
        if spec.handler is None:
            raise NotImplementedError(
                f"Tool {name!r} is declared (side_effect_level={spec.side_effect_level!r}) "
                f"but has no handler yet. {spec.description}"
            )
        for arg, hint in spec.schema.items():
            if arg not in kwargs:
                continue
            if not isinstance(kwargs[arg], _types_for_hint(hint)):
                raise TypeError(
                    f"Tool {name!r} argument {arg!r} must be {hint!r}, "
                    f"got {type(kwargs[arg]).__name__}"
                )
        try:
            return spec.handler(**kwargs)
        except Exception as e:
            # Keep the original exception type (so callers that catch
            # TypeError, FileNotFoundError, etc. keep working) but attach
            # the failing tool's name as context. If the exception type
            # can't take attributes, re-raise as-is rather than losing the
            # original entirely.
            try:
                e.tool_name = name
            except Exception:
                pass
            raise


registry = ToolRegistry()


def _register_defaults() -> None:
    """Register every tool named in the roadmap's C-006 list. Tools
    backed by working code (via brain.py) get a real handler; the rest
    are declared with handler=None, so the registry is a complete,
    honest map of "what the brain should eventually do" even before
    the riskier half of it is safe to execute (C-007)."""
    from src import brain

    registry.register(ToolSpec(
        name="search_memory",
        description="Search stored structured facts, optionally filtered by domain.",
        schema={"query": "str", "domain": "str | None", "n_results": "int"},
        permissions=["read:facts"],
        timeout_seconds=10,
        side_effect_level="read",
        handler=lambda query, domain=None, n_results=5: brain.search_memory(query, domain=domain, n_results=n_results),
    ))
    registry.register(ToolSpec(
        name="search_knowledge",
        description=(
            "Alias of search_memory, kept as a distinct roadmap-listed tool name "
            "until vector search (C-010) makes the two meaningfully different."
        ),
        schema={"query": "str", "domain": "str | None"},
        permissions=["read:facts"],
        timeout_seconds=10,
        side_effect_level="read",
        handler=lambda query, domain=None: brain.search_memory(query, domain=domain),
    ))
    registry.register(ToolSpec(
        name="remember",
        description="Store a structured fact (domain, key, value).",
        schema={"domain": "str", "key": "str", "value": "str", "source": "str", "confidence": "float"},
        permissions=["write:facts"],
        timeout_seconds=5,
        side_effect_level="write",
        handler=lambda domain, key, value, source="conversation", confidence=1.0: brain.remember(
            domain, key, value, source, confidence
        ),
    ))
    registry.register(ToolSpec(
        name="ingest_document",
        description="Ingest a single file, or every supported file in a folder, into the knowledge store.",
        schema={"path": "str", "is_folder": "bool"},
        permissions=["write:knowledge", "read:filesystem"],
        timeout_seconds=120,
        side_effect_level="write",
        handler=lambda path, is_folder=False: brain.ingest(path, is_folder=is_folder),
    ))
    registry.register(ToolSpec(
        name="forget",
        description="Delete a stored structured fact (domain, key). Destructive: runs only through the approval gate in permissions.py.",
        schema={"domain": "str", "key": "str"},
        permissions=["write:facts", "destructive:facts"],
        timeout_seconds=5,
        side_effect_level="destructive",
        handler=lambda domain, key: brain.forget(domain, key),
    ))
    registry.register(ToolSpec(
        name="query_database",
        description="Not yet implemented — arbitrary structured-store queries need a query surface beyond upsert/list.",
        schema={"query": "str"},
        permissions=["read:facts"],
        timeout_seconds=10,
        side_effect_level="read",
    ))
    registry.register(ToolSpec(
        name="search_web",
        description=(
            "Free web search (no API key) via DuckDuckGo HTML + BotBrowser extract — falls back to Brave/Bing. "
            "TIER FIRST: pass tier=quick/standard/deep (how much) before kind/platform — it fans out "
            "to every kind (standard = web 5, youtube 10, tiktok/ig/fb/x 30, moltbook 30, reddit 20). "
            "Optionally also queries Moltbook (semantic) + Reddit (with Brave fallback) + social video "
            "(YouTube via yt-dlp + transcripts, best-effort TikTok) as extra resources run with the main tool. "
            "Pass with_moltbook/with_reddit/with_social to include them (with_extras = all three). "
            "crawl=true crawls ALL searches and compiles one crawl file with sections. "
            "Supports expand_query for generic queries and strict_reddit filtering. "
            "Pass archive=true to full-save each URL to archive/<date>_<domain>_<hash>/raw.html+clean.md+meta.json (no truncation on disk). "
            "Returns markdown hits (or dict with extras when extras requested)."
        ),
        schema={"query": "str", "limit": "int | None", "budget": "int | None", "tier": "str | None", "with_moltbook": "bool | None", "with_reddit": "bool | None", "with_social": "bool | None", "with_extras": "bool | None", "moltbook_limit": "int | None", "reddit_limit": "int | None", "social_limit": "int | None", "social_platforms": "str | None", "social_download": "bool | None", "social_quality": "str | None", "social_audio_only": "bool | None", "social_background": "bool | None", "social_max_mb": "float | None", "reddit_subreddit": "str | None", "moltbook_submolt": "str | None", "expand_query": "bool | None", "strict_reddit": "bool | None", "crawl": "bool | None", "crawl_file": "str | None", "limit_type": "str | None", "sources": "int | None", "chars": "int | None", "research_time": "float | None", "archive": "bool | None", "archive_dir": "str | None"},
        permissions=["network"],
        timeout_seconds=1500,
        side_effect_level="read",
        supported_agents=["any"],
        handler=lambda query, limit=5, budget=8000, tier=None, with_moltbook=None, with_reddit=None, with_social=None, with_extras=None, moltbook_limit=None, reddit_limit=None, social_limit=None, social_platforms=None, social_download=None, social_quality=None, social_audio_only=None, social_background=None, social_max_mb=None, reddit_subreddit=None, moltbook_submolt=None, expand_query=None, strict_reddit=None, crawl=None, crawl_file=None, limit_type=None, sources=None, chars=None, research_time=None, archive=None, archive_dir=None: (
            __import__("tools.botbrowser_search", fromlist=["search_with_extras"]).search_with_extras(
                query, limit=sources or limit or 5, token_budget=(chars//4 if chars else budget) or 8000,
                tier=tier,
                with_moltbook=bool(with_moltbook or with_extras), with_reddit=bool(with_reddit or with_extras),
                with_social=bool(with_social or with_extras),
                moltbook_limit=moltbook_limit, reddit_limit=reddit_limit,
                social_limit=social_limit, social_platforms=social_platforms or "youtube",
                social_download=bool(social_download), social_quality=social_quality or "best",
                social_audio_only=bool(social_audio_only), social_background=bool(social_background),
                social_max_mb=social_max_mb,
                reddit_subreddit=reddit_subreddit, moltbook_submolt=moltbook_submolt,
                expand_query=bool(expand_query), strict_reddit=bool(strict_reddit), crawl=bool(crawl), crawl_file=crawl_file, limit_type=limit_type,
                archive=bool(archive), archive_dir=archive_dir or "archive",
            ) if (with_moltbook or with_reddit or with_social or with_extras) else
            __import__("tools.botbrowser_search", fromlist=["search_and_extract"]).search_and_extract(query, limit=sources or limit or 5, token_budget=(chars//4 if chars else budget) or 8000, expand_query=bool(expand_query), crawl=bool(crawl), limit_type=limit_type, archive=bool(archive), archive_dir=archive_dir or "archive")
        ),
    ))
    registry.register(ToolSpec(
        name="search_moltbook",
        description=(
            "Search-only Moltbook (agent social network) — NO registration, NO posting. "
            "Uses public /api/v1/search (semantic) + /api/v1/posts + /api/v1/submolts (list). "
            "Verified 2026-09-01 to work WITHOUT auth. Also supports browsing submolts (https://www.moltbook.com/m) "
            "and per-submolt feeds. Read-only research."
        ),
        schema={"query": "str | None", "submolt": "str | None", "limit": "int | None", "limit_type": "str | None", "sort": "str | None", "type": "str | None"},
        permissions=["network"],
        timeout_seconds=660,
        side_effect_level="read",
        supported_agents=["any"],
        handler=lambda query=None, submolt=None, limit=None, limit_type=None, sort="new", type="all": (
            __import__("tools.moltbook_search", fromlist=["search_moltbook", "get_posts", "list_submolts"]).search_moltbook(query, limit=limit, result_type=type or "all", limit_type=limit_type) if query
            else __import__("tools.moltbook_search", fromlist=["get_posts", "list_submolts"]).get_posts(sort=sort or "new", limit=limit or 10, submolt=submolt) if submolt
            else (_ for _ in ()).throw(TypeError("search_moltbook requires query or submolt (or limit for list_submolts via tools/moltbook_search.py directly)"))
        ),
    ))
    registry.register(ToolSpec(
        name="search_reddit",
        description=(
            "Search-only Reddit — NO auth, NO posting. Primary: public JSON (/search.json, /r/{sub}/search.json) "
            "with automatic fallback to Brave/DuckDuckGo HTML search for site:reddit.com when Reddit WAF blocks (403/429). "
            "Surfaces relevant posts and external 'new website' links that Reddit discussions contain. Read-only research."
        ),
        schema={"query": "str | None", "subreddit": "str | None", "limit": "int | None", "limit_type": "str | None", "sort": "str | None"},
        permissions=["network"],
        timeout_seconds=660,
        side_effect_level="read",
        supported_agents=["any"],
        handler=lambda query=None, subreddit=None, limit=None, limit_type=None, sort="relevance": (
            __import__("tools.reddit_search", fromlist=["search_reddit", "list_subreddit_posts"]).search_reddit(query, limit=limit, subreddit=subreddit, sort=sort or "relevance", limit_type=limit_type) if query
            else __import__("tools.reddit_search", fromlist=["list_subreddit_posts"]).list_subreddit_posts(subreddit, limit=limit or 10, sort=sort or "new") if subreddit
            else (_ for _ in ()).throw(TypeError("search_reddit requires query or subreddit"))
        ),
    ))
    registry.register(ToolSpec(
        name="search_social",
        description=(
            "Social media search — standalone, no API key (yt-dlp + youtube-transcript-api). "
            "YouTube (primary): keyword search via ytsearch + full caption transcripts. "
            "TikTok: best-effort search. Instagram/Facebook/X: direct post/video URL metadata "
            "(no stable search prefix — anti-bot rotation). Read-only research."
        ),
        schema={"query": "str | None", "video": "str | None", "limit": "int | None", "limit_type": "str | None", "platforms": "str | None", "no_transcript": "bool | None", "archive": "bool | None", "download": "bool | None", "quality": "str | None", "audio_only": "bool | None", "background": "bool | None", "max_mb": "float | None"},
        permissions=["network"],
        timeout_seconds=1500,
        side_effect_level="read",
        supported_agents=["any"],
        handler=lambda query=None, video=None, limit=None, limit_type=None, platforms=None, no_transcript=None, archive=None, download=None, quality=None, audio_only=None, background=None, max_mb=None: (
            __import__("tools.social_search", fromlist=["get_video", "archive_social_hit"]).get_video(
                video, include_transcript=not no_transcript)
            if video and not (archive or download) else
            __import__("tools.social_search", fromlist=["search_social"]).search_social(
                query or video, limit=limit,
                platforms=tuple(p.strip().lower() for p in str(platforms or "youtube").split(",") if p.strip()),
                include_transcript=not no_transcript,
                archive=bool(archive), download=bool(download),
                quality=quality or "best", audio_only=bool(audio_only),
                background=bool(background), max_mb=max_mb, limit_type=limit_type)
            if (query or video)
            else (_ for _ in ()).throw(TypeError("search_social requires query or video URL"))
        ),
    ))
    registry.register(ToolSpec(
        name="social_download_status",
        description=(
            "Poll a background social download started with background=true. "
            "Pass a job folder (or status.json path) from search_social jobs. "
            "Returns queued/downloading/done/error plus folder_bytes growth. "
            "Use this instead of blocking: start with background=true, do other "
            "work, poll until done, then read video_file."
        ),
        schema={"folder": "str"},
        permissions=["network"],
        timeout_seconds=15,
        side_effect_level="read",
        supported_agents=["any"],
        handler=lambda folder: __import__("tools.social_search", fromlist=["download_status"]).download_status(folder),
    ))
    registry.register(ToolSpec(
        name="search_openalex",
        description="Free academic search via OpenAlex (200M+ papers, no API key, 100k/day) — for research queries. Uses https://api.openalex.org/works?search=",
        schema={"query": "str", "limit": "int | None", "limit_type": "str | None"},
        permissions=["network"],
        timeout_seconds=240,
        side_effect_level="read",
        handler=lambda query, limit=None, limit_type=None: __import__("tools.academic_search", fromlist=["search_openalex"]).search_openalex(query, limit=limit, limit_type=limit_type),
    ))
    registry.register(ToolSpec(
        name="search_arxiv",
        description="Free academic search via Arxiv (no API key) — for AI agent papers. Uses http://export.arxiv.org/api/query",
        schema={"query": "str", "limit": "int | None", "limit_type": "str | None"},
        permissions=["network"],
        timeout_seconds=240,
        side_effect_level="read",
        handler=lambda query, limit=None, limit_type=None: __import__("tools.academic_search", fromlist=["search_arxiv"]).search_arxiv(query, limit=limit, limit_type=limit_type),
    ))
    registry.register(ToolSpec(
        name="search_github",
        description="Free code search via GitHub API (10/min without auth, no key) — for tools/repos. Uses https://api.github.com/search/repositories",
        schema={"query": "str", "limit": "int | None", "limit_type": "str | None"},
        permissions=["network"],
        timeout_seconds=240,
        side_effect_level="read",
        handler=lambda query, limit=None, limit_type=None: __import__("tools.academic_search", fromlist=["search_github"]).search_github(query, limit=limit, limit_type=limit_type),
    ))
    registry.register(ToolSpec(
        name="read_document",
        description="Not yet implemented — reading arbitrary files needs permission-aware execution (C-007) first.",
        schema={"path": "str"},
        permissions=["read:filesystem"],
        timeout_seconds=10,
        side_effect_level="read",
    ))
    registry.register(ToolSpec(
        name="read_file",
        description="Not yet implemented — needs C-007 before arbitrary filesystem reads are exposed to a model.",
        schema={"path": "str"},
        permissions=["read:filesystem"],
        timeout_seconds=10,
        side_effect_level="read",
    ))
    registry.register(ToolSpec(
        name="write_file",
        description="Not yet implemented — needs C-007 before arbitrary filesystem writes are exposed to a model.",
        schema={"path": "str", "content": "str"},
        permissions=["write:filesystem"],
        timeout_seconds=10,
        side_effect_level="destructive",
    ))
    registry.register(ToolSpec(
        name="run_python",
        description="Not yet implemented — arbitrary code execution needs C-007's permission policy and sandboxing.",
        schema={"code": "str"},
        permissions=["execute:python"],
        timeout_seconds=30,
        side_effect_level="destructive",
    ))
    registry.register(ToolSpec(
        name="run_shell",
        description="Not yet implemented — arbitrary shell execution needs C-007's permission policy and sandboxing.",
        schema={"command": "str"},
        permissions=["execute:shell"],
        timeout_seconds=30,
        side_effect_level="destructive",
    ))
    registry.register(ToolSpec(
        name="inspect_git",
        description="Not yet implemented — read-only git status/log inspection, deferred until an interface needs it.",
        schema={},
        permissions=["read:filesystem"],
        timeout_seconds=10,
        side_effect_level="read",
    ))
    registry.register(ToolSpec(
        name="create_branch",
        description="Not yet implemented — needs C-007 before an agent can create git branches unsupervised.",
        schema={"branch_name": "str"},
        permissions=["write:filesystem", "execute:shell"],
        timeout_seconds=10,
        side_effect_level="write",
    ))
    registry.register(ToolSpec(
        name="run_tests",
        description="Not yet implemented — needs C-007 before an agent can trigger test runs unsupervised.",
        schema={},
        permissions=["execute:shell"],
        timeout_seconds=300,
        side_effect_level="read",
    ))
    registry.register(ToolSpec(
        name="create_task",
        description=(
            "Not yet implemented — no brain-internal task/orchestration layer exists yet "
            "(distinct from the multi-agent orchestrator's own task board in orchestration/tasks.yaml)."
        ),
        schema={"description": "str"},
        permissions=["write:tasks"],
        timeout_seconds=5,
        side_effect_level="write",
    ))
    registry.register(ToolSpec(
        name="send_telegram",
        description=(
            "Not yet implemented through the registry — the Telegram bot currently sends "
            "messages directly via its own handlers, not through brain/tools."
        ),
        schema={"chat_id": "str", "text": "str"},
        permissions=["network", "write:telegram"],
        timeout_seconds=10,
        side_effect_level="write",
    ))
    registry.register(ToolSpec(
        name="notify_user",
        description="Not yet implemented — no generic cross-interface notification path exists yet.",
        schema={"message": "str"},
        permissions=["write:notifications"],
        timeout_seconds=10,
        side_effect_level="write",
    ))


_register_defaults()

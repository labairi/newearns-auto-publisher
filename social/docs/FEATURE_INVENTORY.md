# ShapelessAI feature inventory (reference) and what this tool copies

**How this was verified.** The public site (shapelessai.com, /docs, /pricing, /free) could not be fetched from the build environment (blocked). The inventory below comes from the owner's own authenticated ShapelessAI connector (its capability and platform listings), so it is verified for that account at the time checked (2026-10-22) but nothing was read from the public marketing or docs pages, and nothing about its internals is assumed. Pricing and credit costs were not read.

| ShapelessAI capability (listed live) | This tool |
|---|---|
| Create posts: now, at a time, or in the next queue slot | Yes: draft, schedule at a time, publish now. No "next free slot" |
| Review queue: list, approve, edit, cancel, mark posted, revise | Queue, edit, cancel, mark published. No approval step (you are the only user) |
| Posting schedule (weekly slots per account) | No |
| Platforms: LinkedIn, LinkedIn page, X, Instagram, TikTok, YouTube, Facebook Page, Threads, Bluesky, Mastodon, Discord (Reddit, Telegram, Pinterest pending) | Bluesky, Mastodon, Threads, Facebook Page, Instagram. The rest not built |
| Per-platform text limits and media rules | Yes (rules.py, mirrored in the dashboard) |
| Analytics (metrics, history, followers) | Publication history only |
| Brand memory | `config/brand.json` used in the AI prompt |
| AI media generation, video editing, carousels, thumbnails, voiceover, subtitles, demo recording | No |
| Agent team: jobs, standing agents | No (you generate text in your own assistant and import it) |
| Inbox / comments, Meta ads boost, profile audit, references, trends | No |
| MCP, CLI, REST access | CLI yes. MCP/REST no |
| Free plan: 10 posts/day | No daily quota of its own; platform limits apply |

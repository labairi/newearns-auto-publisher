# Site Brief — New Earns

> Read by: keyword-fanout-map · seo-content-writer · onpage-optimizer ·
> internal-link-architect · ai-visibility-checker
>
> Anything marked ⚠️ verify was inferred from the website and needs a human to
> confirm it. Anything marked ⚠️ NEEDS INPUT could not be determined at all.
>
> Last updated: 2026-10-21 (built from a crawl of newearns.com, its public
> WordPress REST API, and this repo's `config.yaml` / `publisher/writer.py`).
> Search Console and Analytics are not connected: this brief contains no traffic,
> ranking or search-volume figures, and none should be added without a real source.

---

## Part 1 — Business

- **Business name:** New Earns (the site styles itself "Newearns.com")
- **Domain:** https://newearns.com
- **Industry / niche:** Make-money-online and online-tools blog. The newer content
  (since 2026-10-20) focuses on practical how-tos for freelancers and creators:
  PDF/document tasks and Instagram creator analytics.
- **What they actually sell (one sentence):** A free, ad-supported how-to blog for
  people earning money online, which also points readers to the owner's two tools,
  Private PDF Editor and Instagramaps, where they solve the step the reader is on. ⚠️ verify
- **Price positioning:** Free content; the linked products have free tiers and
  paid plans (Instagramaps paid plans start at 9 USD/month per its pricing page as of
  2026-10-19; Private PDF Editor "has paid plans", no price claimed).

### Services or products

1. **Editorial guides** (the blog itself). Revenue from display ads: Google AdSense
   review submitted. ⚠️ verify that ads are the main revenue goal.
2. **Private PDF Editor** — https://private-pdf-editor.com/ — browser-based PDF
   toolkit with about 30 tools (compress, merge, split, remove pages, protect, sign,
   convert, invoice generator, image to text). Allowed claims live in
   `config.yaml > products`.
3. **Instagramaps** — https://instagramaps.com/ — connect your own Instagram account
   to see where your audience engages by country and city, compare views/reach/
   engagement per post, and a creator–business marketplace.

**Money pages** (where internal links should lead):
- https://newearns.com/compress-pdf-for-email/ (links to Private PDF Editor)
- https://newearns.com/instagram-follower-location/ (links to Instagramaps)
- Future calendar posts in the PDF and Instagram pillars (see `config.yaml > calendar`).
- ⚠️ verify: the product sites themselves are the real conversion points; New Earns
  has no on-site sign-up, email list or shop that the crawl found.

### Service area

- **Primary location(s):** Online, no physical service area. Owner is based in
  Morocco (UTC+0); the site shows no address. ⚠️ verify
- **Towns / suburbs / regions that matter:** Not applicable (global, English-language).
- **Country for search data:** ⚠️ NEEDS INPUT — the site does not target one
  country. Working assumption until confirmed: **United States**. Note that sources
  cited in articles are US/global (FTC, Adobe, Meta Help Center).
- **Language for search data:** English (`en`; `config.yaml > site.language: en`).

> These last two are load-bearing. Wrong values return wrong search volumes in
> every skill, with no error message.

### Competitors

⚠️ NEEDS INPUT. Guesses from the topics the site now covers, not from SERP data:

1. smallpdf.com / ilovepdf.com blogs (PDF how-tos) ⚠️ verify
2. later.com / hootsuite.com blogs (Instagram analytics how-tos) ⚠️ verify
3. sidehustlenation.com (make-money-online guides) ⚠️ verify

### Goals

- **Primary goal:** Rank and get cited for practical how-to queries in the PDF and
  Instagram-creator pillars, earn AdSense approval, and send relevant readers to
  the two products. ⚠️ verify the order of priority
- **What a customer is worth, roughly:** ⚠️ NEEDS INPUT
- **What this business will NOT do:**
  - Invent statistics, testimonials, case studies, prices, limits or features.
  - Claim first-hand testing ("I tested", "in my experience").
  - Push the products where they don't solve the reader's step (max 2 product
    links per article, always with the ownership disclosure).
  - Disparage competitors.
  - Modify AdSense, Site Kit, publisher IDs, ads.txt or verification code.

### Proof and assets

- **Real numbers the business can claim:** ⚠️ NEEDS INPUT. None found on the site.
  (Older posts contain unsourced figures, e.g. "global gaming market is worth over
  $200 billion"; these are not proof and should not be reused.)
- **Case studies / results that actually exist:** ⚠️ NEEDS INPUT. None found.
- **Certifications, awards, associations, memberships:** None found.
- **Years in business / team size:** ⚠️ NEEDS INPUT. The About page shows a first-
  published date of 2018-11-24, but the oldest blog post is dated 2025-07-21, so
  "since 2018" should not be claimed without confirmation.

### Author identity (for author bios and E-E-A-T)

- **Who is credited as author:** Inconsistent today. ⚠️ NEEDS INPUT
  - Older posts (Jul–Sep 2025) and the About page credit **"David"**, described as a
    "digital explorer" and "side hustle strategist" who has "tested dozens of
    methods". Author archive: https://newearns.com/author/lahcenlabairigmail-com/
  - New posts (3695, 3699) are credited to **"New Earns"** (the publisher's WP user)
    and are written as an editorial guide with no personal testing claims.
- **Their credentials, in one line:** ⚠️ NEEDS INPUT
- **Author page URL:** see above; no dedicated author bio page found.

### The call to action

- **Primary CTA:** In-article product link where it fits the task (e.g. "Remove
  pages the recipient doesn't need" → Private PDF Editor). No newsletter or lead
  magnet found. ⚠️ verify
- **Where it points:** private-pdf-editor.com or instagramaps.com with
  `utm_source=newearns&utm_medium=referral&utm_campaign={slug}`
- **What happens after the click:** Reader lands on the product's tool/home page;
  free use, paid plans available. ⚠️ verify
- **Phone:** None published.
- **Address (exactly as it should appear everywhere):** None published.
- **Contact:** contact@newearns.com, form at https://newearns.com/contact/
  ("We usually respond within 24–48 hours"). Social: instagram.com/newearns,
  twitter.com/newearns, fb.com/newearns.

---

## Part 2 — Voice

Two voices exist on the site. The **new editorial voice** (posts from 2026-10-20 on,
and the rules in `publisher/writer.py`) is the target. The older 2025 voice
(upbeat, "proven", "skyrocket", "The Shocking Truth") is what to move away from.

### Tone in three words

Practical, careful, plain.

### The reader

- **Who is reading:** Freelancers, small-business owners, creators and people
  starting online work, usually mid-task (a file won't attach, they want to know
  where their followers are). Beginners in the older posts.
- **Reading level:** Plain English, roughly general-audience / grade 8–9. ⚠️ verify
- **What they are afraid of:** Scams and wasted time (the About page's founding
  story is about avoiding scams); a file or account step failing at the worst
  moment; paying for something they didn't need. ⚠️ verify
- **What would make them trust this business:** Steps that match the current
  version of the tool, links to official documentation, honest limits ("what it
  doesn't protect"), and clear disclosure when a product is the owner's.

### Say / don't say

| Always | Never |
|--------|-------|
| Link every number or changing fact to its source in the same paragraph (vendor docs first) | Invent statistics, quotes, testimonials, case studies, prices or limits |
| "Disclosure: {product} is built by the team behind New Earns. We mention it where it fits the task; the steps in this guide also work with other tools." | "I tested", "we tested", "in my experience", "our team tested", "I personally" |
| Say when a menu or UI may differ by app version | "guaranteed", "100% safe", "skyrocket", "shocking truth" |
| Name the free/built-in option first (Preview, Acrobat, Instagram's own Insights) | "In today's digital world" / filler intros |
| Say what a tool can't do (e.g. no per-follower location) | Claim Instagramaps can see accounts the user doesn't own or private data |
| ⚠️ NEEDS INPUT: owner's own preferred words | ⚠️ NEEDS INPUT: any other words the owner never wants used |

### Sentence style

- **Person:** Second person ("you", "your") for instructions; "we" only in the
  disclosure. No first-person-singular experience claims.
- **Contractions:** Yes ("won't", "you're", "it's").
- **Humour:** None.
- **Sentence length:** Mostly medium, with short imperative lines for emphasis
  ("Keep the original.").
- **Anything structural to always do or never do:**
  - Open with the reader's problem in one line, then what the guide covers.
  - Descriptive H2s that say what the section does (not keyword-stuffed headings).
  - Numbered steps for procedures; a Troubleshooting section and an FAQ at the end.
  - Focus keyphrase appears naturally (title, intro, once or twice more).
  - 1,200–3,200 words; at least 3 sources; HTTPS links only; no AI-chat utm
    parameters (e.g. `utm_source=chatgpt.com`) on any link.

### Words from the business's own mouth

- "Your PDF won't attach, or the email comes back undelivered." (compress-pdf-for-email)
- "Instagram can show you the top countries and cities your followers come from,
  but only as totals" (instagram-follower-location)
- "Discover proven ways to earn money online with Newearns.com." (site meta description — older voice)
- "Your Gateway to Online Income" (site tagline)

---

## Site findings from the crawl (not fixed; WordPress was not touched)

1. **Broken/odd links in the hand-published PDF post** (https://newearns.com/compress-pdf-for-email/):
   the H2 "Use a browser-based compressor" links to the unrelated "SEO Marketing
   Digital" post, and the H2 "Check the compressed file before you send it" links to
   `https://newearns.com/protect-pdf/?utm_source=chatgpt.com`, which did not load
   (likely a 404) and carries an AI-chat utm. Fix in WP Admin by hand.
2. **Homepage and category pages don't list the two new posts** (3695, 3699) even
   though the REST API shows them published. Likely a LiteSpeed page cache that
   needs purging. ⚠️ verify
3. **Author mismatch**: "David" persona with testing claims vs "New Earns" editorial
   byline (see Author identity).
4. **Tools & Resources page lists no tools**, although it says it shows "the exact
   tools I personally use". A natural home for honest links to the two products.
5. **Category mismatch**: older money posts like "The Shocking Truth About Personal
   Loans Online" sit in "Passive Income Ideas"; new PDF/Instagram posts sit in "Make
   Money Online" because the proposed Documents & Productivity / Creator Economy
   categories don't exist yet.
6. **Sitemap is healthy**: https://newearns.com/sitemap.xml is a valid AIOSEO index
   (post, page, category, tag and additional sitemaps).
7. **No affiliate disclosure** on older posts that link to third-party platforms;
   no structured-data types were visible to the crawler on the new posts. ⚠️ verify

---

## Hard content rules

Things no skill may ever do on this site:

- Never modify AdSense, Site Kit, publisher IDs, ads.txt or verification code.
- Never invent traffic, ranking or search-volume figures (Search Console and
  Analytics are not connected).
- Never claim first-hand testing or personal experience.
- Link Private PDF Editor or Instagramaps only where it solves the reader's current
  step, at most twice per article, with the ownership disclosure right after the
  first mention, and only with claims from `config.yaml > products`.
- Never invent statistics, results, reviews or credentials.
- Never cite a source that was not fetched and confirmed in that run.

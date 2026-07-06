# Data Sourcing Decisions

Date: 2026-05-03
Status: LOCKED

These decisions lock the data sourcing strategy for the Hockey Talent ID Engine.
Changes require explicit re-decision.

---

## 1. Historical Data Horizon

**Decision:** 5-year bootstrap window, indefinite retention going forward.

**Rationale:** 5 years provides sufficient baseline for trend analysis and contextual comparison without excessive upfront scraping burden. All new data retained indefinitely so the dataset naturally expands over time.

**Implications:**
- Initial ingestion: ~5 years of NHL game data (play-by-play, shifts, box scores)
- Junior leagues: 5 years where data is available
- Storage: PostgreSQL with partitioning strategy for large tables

---

## 2. Junior / Development League Priority

**Decision:** Include ALL major North American development leagues.

**Leagues Included:**
- OHL (Ontario Hockey League)
- WHL (Western Hockey League)
- QMJHL (Quebec Major Junior Hockey League)
- USHL (United States Hockey League)
- NCAA (Division I hockey)
- AHL / NHL-adjacent pro development contexts

**Rationale:** Elite talent develops across multiple pathways. Missing any major pipeline creates blind spots in the evaluation engine.

**Implications:**
- Must scrape or API-pull from each league's official source
- EliteProspects as fallback aggregator for cross-league player matching
- Consider EliteStats API (~$30/mo) for reliable junior data at scale

---

## 3. European / International League Coverage

**Decision:** European leagues are important. Include major and secondary pathways.

**Priority Leagues:**
- KHL (Kontinental Hockey League)
- SHL / Swedish pro leagues
- Liiga (Finnish top division)
- DEL (German top division)
- Czech Extraliga
- Swiss National League
- Other meaningful European pro leagues

**Also Include:** Less conventional pathways where data exists. Non-traditional development routes can produce elite talent.

**Rationale:** European development pipelines are a major source of NHL talent. Ignoring them creates systematic blind spots, especially for players who develop outside North America.

**Implications:**
- EliteProspects is the primary source for European league data
- Scraping complexity increases with more leagues
- Player ID matching across leagues becomes critical

---

## 4. Video Clip Integration

**Decision:** YES â store links/references to relevant video clips within player/evaluation records.

**Implementation:**
- `video_clips` table linked to player evaluations
- Store URLs (YouTube, NHL.com, etc.) with metadata (date, context, clip type)
- No video hosting â links only
- Tied directly to scouting notes and trait evaluations

**Rationale:** Film evidence is essential for credible scouting. Links provide traceable evidence for every evaluation claim.

**Implications:**
- Additional schema table for video references
- Manual entry initially (no automated clip detection)
- Future: potential integration with clip services

---

## 5. Data Refresh Frequency

**Decision:**
- **Daily during season** for active leagues / player tracking
- **Weekly during offseason** for maintenance / slower-moving updates

**Rationale:** Season data changes daily (games, stats, injuries). Offseason changes are slower (transfers, contracts, development updates).

**Implications:**
- Scheduled ingestion pipeline with season-aware frequency
- Need season calendar tracking (NHL, CHL, NCAA, European seasons differ)
- Background job scheduler required

---

## 6. Tech Stack Decisions (from prior session)

**Database:** PostgreSQL (longevity, reliability, JSONB support)
**Language:** Python (data science ecosystem, scraping libraries)
**Interface:** CLI-first, notebook for analysis, web UI later
**Storage:** Local-first, no cloud dependencies initially

---

## 7. Data Sources Locked In

### Primary (Free)
- NHL Stats API (`statsapi.web.nhl.com`) â NHL core data
- Natural Stat Trick â zone starts, QoC, QoT
- Evolving Hockey â RAPM, transition metrics
- MoneyPuck / CapFriendly â contract data
- EliteProspects â cross-league player profiles, European data
- League official sites (OHL, WHL, QMJHL, USHL, NCAA) â basic stats

### Secondary (Paid, Optional)
- EliteStats API (~$30/mo) â structured junior league data
- EliteProspects Premium (~$50/yr) â deeper player data

**Start Strategy:** Free tier only. Add paid sources when scraping proves insufficient.

---

## Next Steps

Phase 0.1 (Data source research) is COMPLETE.

Moving to Phase 0.2: Schema design and Phase 1: Foundation.

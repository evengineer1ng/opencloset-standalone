# Hockey Data Source Research

Survey of available hockey data APIs and sources for the Talent ID Engine.
Date: 2026-05-03

---

## 1. NHL Data (Primary League)

### 1.1 NHL Stats API (Unofficial but Stable)

**URL:** `https://statsapi.web.nhl.com`
**Cost:** FREE (no API key required)
**Rate Limits:** None enforced, but be polite (~1 req/sec)
**Documentation:** Unofficial. Reverse-engineered by community. See [NHL API Wiki](https://gitlab.com/dword4/nhlapi)

**What it provides:**
- Player rosters, demographics, contracts
- Game-by-game box scores (all-time)
- Shift-by-shift data (play-by-play with timestamps)
- Play-by-play events (shots, hits, blocks, penalties, faceoffs)
- Season/career aggregates per player
- Team schedules, standings, injuries
- Playoff data
- Draft history

**Key endpoints:**
- `/api/v1/people` - Player search/lookup
- `/api/v1/people/{id}` - Player details
- `/api/v1/people/{id}/stats` - Player stats by season
- `/api/v1/game/{id}/feed/live` - Live game feed (shifts, events)
- `/api/v1/schedule` - Team schedules
- `/api/v1/teams` - Team info

**Strengths:**
- Free, no signup
- Covers entire NHL history (1990s onward for detailed data)
- Shift-level granularity is rare and valuable
- Play-by-play lets you derive advanced metrics yourself

**Limitations:**
- No advanced analytics (Corsi, Fenwick, RAPM, zone starts) Ã¢ÂÂ you compute these
- No video clips
- No junior/minor league data
- No scouting reports
- Unofficial Ã¢ÂÂ could break (but has been stable for 10+ years)

---

### 1.2 Natural Stat Trick (NST)

**URL:** `https://www.naturalstattrick.com`
**Cost:** FREE (web scraping possible; no official API)
**Rate Limits:** N/A (scraping)

**What it provides:**
- Zone start percentages (oZS%, dZS%)
- Quality of competition (QoC)
- Quality of teammates (QoT)
- Corsi/Fenwick adjusted for zone starts
- Rush stats, high-danger chances
- Deployment analysis by coach/system

**Strengths:**
- Best free source for deployment context
- Critical for our "contextual layer" (Phase 3)
- Updated regularly during season

**Limitations:**
- No official API Ã¢ÂÂ requires scraping
- Seasonal data only (not historical deep)
- NHL only

---

### 1.3 Evolving Hockey

**URL:** `https://evolvinghockey.com`
**Cost:** FREE (web scraping possible; no official API)
**Rate Limits:** N/A (scraping)

**What it provides:**
- RAPM (Regularized Adjusted Plus-Minus)
- Zone-level RAPM (offensive/defensive zone entries/exits)
- Transition metrics
- Power play / penalty kill RAPM
- Player-on-ice goals for/against

**Strengths:**
- Best free source for on-ice impact metrics
- RAPM is the gold standard for isolating player contribution
- Transition metrics are unique

**Limitations:**
- No official API Ã¢ÂÂ requires scraping
- Seasonal data only
- NHL only

---

### 1.4 MoneyPuck

**URL:** `https://www.moneypuck.com`
**Cost:** FREE (web scraping possible; no official API)
**Rate Limits:** N/A (scraping)

**What it provides:**
- Contract data (cap hits, AAV, contract length)
- Salary cap analysis
- Free agent history
- Trade history

**Strengths:**
- Best source for contract/financial data
- Useful for understanding player value context

**Limitations:**
- No official API Ã¢ÂÂ requires scraping
- Financial data only, not performance

---

### 1.5 NHL.com (Official)

**URL:** `https://www.nhl.com`
**Cost:** FREE
**Rate Limits:** Standard web scraping limits

**What it provides:**
- Standard box score stats
- Player profiles
- News, injuries
- Some advanced stats (limited)

**Strengths:**
- Official source
- Always available

**Limitations:**
- Limited advanced stats
- No shift-level data
- Scraping is fragile (site changes break scrapers)

---

### 1.6 Paid NHL Data Providers

#### The Athletic / Warsaw Stats
- **Cost:** Subscription (~$50-100/year for The Athletic)
- **Coverage:** NHL analytics articles, some proprietary metrics
- **API:** No API. Article-based consumption only.
- **Verdict:** Not useful for our pipeline.

#### NHL Edge / NHL Analytics
- **Cost:** Unknown (internal NHL product)
- **Coverage:** NHL-internal advanced metrics
- **API:** Not publicly available
- **Verdict:** Not accessible.

#### Sportradar / Geny
- **Cost:** Enterprise pricing ($$$, likely $10k+/year)
- **Coverage:** Full play-by-play, video, global sports
- **API:** Yes, robust REST API
- **Verdict:** Overkill and too expensive for our use case.

#### NHL Data Company (NHLDC)
- **Cost:** Unknown (B2B)
- **Coverage:** NHL data licensing
- **API:** Possibly
- **Verdict:** Not consumer-accessible.

#### CapFriendly
- **URL:** `https://www.capfriendly.com`
- **Cost:** FREE (web scraping possible)
- **Coverage:** Contract data, cap projections, trade analysis
- **API:** No official API
- **Verdict:** Good supplementary source for contract data.

---

## 2. Junior / Development Leagues (Critical for Scouting)

### 2.1 OHL (Ontario Hockey League)

**URL:** `https://www.ohl.ca`
**Cost:** FREE (basic stats)
**API:** No official API
**Coverage:** Player stats, team standings, schedules
**Limitations:** Basic stats only. No advanced metrics. Scraping required.

### 2.2 WHL (Western Hockey League)

**URL:** `https://www.whlhq.com`
**Cost:** FREE (basic stats)
**API:** No official API
**Coverage:** Player stats, team standings, schedules
**Limitations:** Basic stats only. No advanced metrics. Scraping required.

### 2.3 QMJHL (Quebec Major Junior Hockey League)

**URL:** `https://www.qmjhl.com`
**Cost:** FREE (basic stats)
**API:** No official API
**Coverage:** Player stats, team standings, schedules
**Limitations:** Basic stats only. No advanced metrics. Scraping required.

### 2.4 CHL (Canadian Hockey League - umbrella)

**URL:** `https://www.chl.ca`
**Cost:** FREE (limited)
**API:** No official API
**Coverage:** Aggregate CHL data, draft eligibility
**Limitations:** Limited depth.

### 2.5 USHL (United States Hockey League)

**URL:** `https://www.ushl.com`
**Cost:** FREE (basic stats)
**API:** No official API
**Coverage:** Player stats, schedules
**Limitations:** Basic stats only.

### 2.6 NCAA / College Hockey

**URL:** `https://www.ncaa.com/hockey`
**Cost:** FREE (basic stats)
**API:** No official API
**Coverage:** Division I stats
**Limitations:** Fragmented across conferences. No unified API.

### 2.7 European Leagues

**Sources:** EliteProspects (`https://www.eliteprospects.com`)
**Cost:** FREE (basic); Premium (~$50/year) for deeper data
**API:** No official API. Scraping possible but aggressive anti-bot measures.
**Coverage:** NHL, CHL, NCAA, European leagues (SHL, Liiga, DEL, etc.), international tournaments
**Strengths:** Most comprehensive hockey database globally. Player profiles, draft history, contracts, stats across all leagues.
**Limitations:** Scraping is difficult. No shift-level data. No advanced metrics.

---

## 3. Aggregator / Third-Party APIs

### 3.1 EliteStats

**URL:** `https://www.elitestats.com`
**Cost:** FREE tier (limited); Paid tiers ($20-100/month)
**API:** Yes, REST API
**Coverage:** NHL, CHL, NCAA, European leagues
**Fields:** Player stats, game logs, team stats, draft info
**Rate Limits:** Varies by tier
**Verdict:** Strong candidate. Paid but affordable. Covers development leagues.

### 3.2 HockeyDB

**URL:** `https://www.hockeydb.com`
**Cost:** FREE (basic); API access unclear
**API:** Limited/unclear
**Coverage:** Historical hockey data, all-time records
**Verdict:** Good for historical context, not for ongoing pipeline.

### 3.3 The Hockey Writers / Puck Pedia

**URL:** Various
**Cost:** FREE
**API:** No
**Coverage:** Articles, analysis
**Verdict:** Not useful for data pipeline.

### 3.4 Curling / Other Sports APIs

**Verdict:** Not relevant.

---

## 4. Video / Clip Sources

### 4.1 NHL Gamecenter

**URL:** `https://www.nhl.com/gamecenter`
**Cost:** FREE (highlights); NHL.tv subscription (~$50/month) for full games
**API:** No official API for clips
**Coverage:** All NHL games
**Verdict:** Full game access useful but expensive. Highlights are free.

### 4.2 YouTube / Social Media

**Coverage:** Scouting clips, highlight reels
**Verdict:** Unstructured. Not pipeline-viable but useful for manual scouting.

### 4.3 Puckworld / Hockey Clips Services

**Cost:** Various (some free, some paid)
**Verdict:** Fragmented. Not pipeline-viable.

---

## 5. Recommended Data Strategy

### Tier 1: Core Pipeline (Free, Start Here)

| Source | What It Gives Us | How |
|--------|------------------|-----|
| **NHL Stats API** | Player data, game events, shifts, box scores | Direct API calls |
| **Natural Stat Trick** | Zone starts, QoC, QoT, deployment | Scraping |
| **Evolving Hockey** | RAPM, transition metrics | Scraping |
| **MoneyPuck / CapFriendly** | Contract data, cap info | Scraping |

**Total Cost: $0**

This gives us everything we need for NHL players: performance stats, advanced metrics, deployment context, and contract info.

### Tier 2: Junior / Development Data (Free, Scraping Required)

| Source | What It Gives Us | How |
|--------|------------------|-----|
| **EliteProspects** | Player profiles across all leagues | Scraping (careful) |
| **OHL / WHL / QMJHL / USHL** | Basic stats for junior players | Scraping |
| **NCAA.com** | College hockey stats | Scraping |

**Total Cost: $0** (but scraping is fragile and rate-limited)

### Tier 3: Paid Enhancement (Optional, ~$20-50/month)

| Source | What It Gives Us | Cost |
|--------|------------------|------|
| **EliteStats API** | Structured API for CHL/NHL/NCAA | ~$20-50/month |
| **EliteProspects Premium** | Deeper player data | ~$50/year |

**Verdict:** Start with Tier 1 + Tier 2 (free). Add EliteStats API when we need reliable junior league data at scale.

---

## 6. Key Fields Available (NHL Stats API)

### Player Stats Per Game:
- Goals, Assists, Points, +/-, PIM
- Shots on Goal, Shot Percentage
- Time on Ice (TOI), TOI per game
- Faceoff W/L, Faceoff %
- Hits, Blocked Shots, Takeaways, Giveaways
- Power Play Points, SH Points
- Game Winning Goals, OT Goals

### Player Stats Per Season (Aggregates):
- All above, summed/averaged
- Playoff vs Regular Season splits
- Per-game averages

### Play-by-Play Events:
- Event type (shot, hit, penalty, faceoff, etc.)
- Event coordinates (x,y on ice)
- Event timestamp
- Player involved
- Shift context

### Shift Data:
- Shift start/end times
- Shift duration
- Events during shift
- Zone of shift start/end

### Advanced (Computed from Play-by-Play):
- Corsi (shot attempts for/against)
- Fenwick (unweighted shot attempts)
- PDO (S% + SV%)
- Zone start % (from NST)
- RAPM (from Evolving Hockey)

---

## 7. Data Pipeline Architecture (Proposed)

```
[NHL Stats API] âââ
                  â
[NST Scraping] ââââ¼ââ> [Ingestion Pipeline] ââ> [PostgreSQL]
                  â
[Evolving Hockey]ââ¤
                  â
[EliteProspects] ââ
```

1. **Ingestion scripts** (Python) pull data on a schedule (daily/weekly)
2. **Normalization layer** maps all sources to our unified schema
3. **PostgreSQL** stores everything with source attribution and timestamps
4. **Evaluation layer** (our scouting grades) stored alongside objective data

---

## 8. Open Questions â RESOLVED

All open questions answered in [data-sourcing-decisions.md](data-sourcing-decisions.md).

1. **Refresh frequency** Ã¢ÂÂ Daily during season, weekly during off-season
2. **Historical horizon** Ã¢ÂÂ 5-year bootstrap, indefinite retention going forward
3. **Video clip integration** Ã¢ÂÂ Yes, store links/references in player/evaluation records
4. **Junior league priority** Ã¢ÂÂ All major NA development leagues: OHL/WHL/QMJHL/USHL/NCAA
5. **European coverage** Ã¢ÂÂ Important: KHL, SHL, Liiga, DEL, Czech, Swiss, and non-traditional pathways

---

## 9. Cost Summary

| Approach | Monthly Cost | Coverage |
|----------|-------------|----------|
| Free tier (NHL API + scraping) | $0 | NHL + basic junior |
| Free + EliteStats API | ~$20-50/mo | NHL + CHL + NCAA + European |
| Free + EliteStats + EliteProspects Premium | ~$25-55/mo | Full global coverage |
| Enterprise (Sportradar) | $10k+/year | Everything, but overkill |

**Recommendation:** Start with $0. Add EliteStats API (~$30/mo) when we need reliable junior data.

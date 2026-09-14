import time
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

class SocialNewsAgent:
    """
    BEAST v2 Real News & Catalyst Intelligence Agent.
    
    Principles:
    1. Zero Fake/Simulated News: Never generate synthetic sentiment to satisfy a gate.
    2. Epistemic Separation: FACT (Tier 1 SEC/IR) vs REPORT (Tier 2 Financial Press) vs SOCIAL CLAIM (Tier 3).
    3. Explicit UNKNOWN State: If genuine verified news is unavailable, NEWS_STATE = UNKNOWN.
       Technical setups proceed freely with NEWS = UNKNOWN.
    4. Adverse News Veto: Material verified adverse disclosures (fraud, SEC subpoena, guidance crash) veto trades.
    5. Social Manipulation Detection: Detects bot bursts, sybil rings, duplicate spam.
    6. News/Price Divergence: Detects when price movement contradicts news flow.
    """
    def __init__(self):
        self.tier1_domains = [
            "sec.gov", "investor.", "ir.", "press.", "prnewswire.com", "businesswire.com",
            "globenewswire.com", "federalreserve.gov", "delta.exchange"
        ]
        self.tier2_domains = [
            "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com",
            "marketwatch.com", "barrons.com", "finance.yahoo.com"
        ]
        self.tier3_sources = [
            "x.com", "twitter.com", "reddit.com", "stocktwits.com", "telegram", "discord"
        ]

    def _strip_symbol(self, token_symbol: str) -> str:
        clean = token_symbol.replace("USD", "")
        for suffix in ["X", "B", "ON"]:
            if clean.endswith(suffix):
                clean = clean[:-len(suffix)]
                break
        return clean

    def analyze_news_feed(self, token_symbol: str, raw_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Evaluate social and news stream for a given token.
        If raw_items is empty/None, returns NEWS_STATE = UNKNOWN without synthesizing fake data.
        """
        stock_ticker = self._strip_symbol(token_symbol)

        # Explicit UNKNOWN State when no genuine data is present
        if not raw_items:
            return {
                "asset": stock_ticker,
                "token_symbol": token_symbol,
                "news_state": "UNKNOWN",
                "event": "NO_VERIFIED_NEWS",
                "sentiment": 0.0,
                "usable_sentiment": 0.0,
                "confidence": 0.0,
                "source_quality": 0.0,
                "authenticity_score": 1.0,
                "cross_source_agreement": 1.0,
                "emotion": {"fear": 0.2, "greed": 0.2, "optimism": 0.2, "uncertainty": 0.5},
                "source_counts": {"tier1_primary": 0, "tier2_news": 0, "tier3_social": 0, "total": 0},
                "facts": [],
                "reports": [],
                "social_claims": [],
                "model_interpretation": "UNKNOWN: No verified news or catalyst disclosures active. Non-blocking for technical setups.",
                "gate_passed": True,  # Non-blocking when UNKNOWN!
                "has_adverse_veto": False,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        facts: List[Dict[str, Any]] = []
        reports: List[Dict[str, Any]] = []
        social_claims: List[Dict[str, Any]] = []

        tier1_count = 0
        tier2_count = 0
        tier3_count = 0

        # Anti-bot detection accumulators
        copy_paste_hashes = set()
        duplicate_posts = 0
        bot_account_flags = 0

        for item in raw_items:
            source = item.get("source", "").lower()
            text = item.get("text", "")
            sentiment = float(item.get("sentiment", 0.0))

            # 1. Tier 1 - Primary Source
            if any(t1 in source for t1 in self.tier1_domains) or item.get("is_sec_filing") or item.get("is_ir"):
                tier1_count += 1
                facts.append({
                    "type": "FACT",
                    "source": item.get("source"),
                    "claim": text,
                    "sentiment": sentiment,
                    "verified": True,
                    "timestamp": item.get("timestamp", datetime.now(timezone.utc).isoformat())
                })
            # 2. Tier 2 - High-quality Financial News
            elif any(t2 in source for t2 in self.tier2_domains) or item.get("is_verified_press"):
                tier2_count += 1
                reports.append({
                    "type": "REPORT",
                    "publisher": item.get("source"),
                    "headline": text,
                    "sentiment": sentiment,
                    "timestamp": item.get("timestamp", datetime.now(timezone.utc).isoformat())
                })
            # 3. Tier 3 - Social / Community
            else:
                tier3_count += 1
                norm_text = re.sub(r"[^a-zA-Z0-9]", "", text.lower())[:40]
                if norm_text in copy_paste_hashes:
                    duplicate_posts += 1
                else:
                    copy_paste_hashes.add(norm_text)

                if item.get("account_age_days", 100) < 30:
                    bot_account_flags += 1

                social_claims.append({
                    "type": "SOCIAL_CLAIM",
                    "platform": item.get("source", "X"),
                    "content": text,
                    "sentiment": sentiment,
                    "verified": False,
                    "engagement": item.get("engagement", 0)
                })

        # Anti-Bot & Authenticity Heuristics
        authenticity_score = 1.0
        if tier3_count > 0:
            dup_ratio = duplicate_posts / max(tier3_count, 1)
            bot_ratio = bot_account_flags / max(tier3_count, 1)
            penalty = (dup_ratio * 0.5) + (bot_ratio * 0.5)
            authenticity_score = max(0.15, 1.0 - penalty)
        else:
            authenticity_score = 0.95

        total_sources = tier1_count + tier2_count + tier3_count
        source_quality = ((tier1_count * 1.0) + (tier2_count * 0.85) + (tier3_count * 0.35)) / max(total_sources, 1)

        # Sentiments
        verified_sentiments = [f["sentiment"] for f in facts] + [r["sentiment"] for r in reports]
        raw_news_sentiment = sum(verified_sentiments) / len(verified_sentiments) if verified_sentiments else 0.0

        social_sentiments = [s["sentiment"] for s in social_claims]
        raw_social_sentiment = sum(social_sentiments) / len(social_sentiments) if social_sentiments else 0.0

        diff = abs(raw_news_sentiment - raw_social_sentiment)
        cross_source_agreement = max(0.2, 1.0 - (diff * 0.5))

        if facts or reports:
            raw_sentiment = (raw_news_sentiment * 0.75) + (raw_social_sentiment * 0.25)
        else:
            raw_sentiment = raw_social_sentiment * 0.4  # Heavy discount if only unverified social claims

        # Usable Sentiment Formula
        independence = 0.88 if (tier1_count + tier2_count) >= 2 else 0.50
        usable_sentiment = raw_sentiment * source_quality * independence * authenticity_score

        # Confidence
        base_conf = 0.50
        if tier1_count >= 1: base_conf += 0.25
        if tier2_count >= 2: base_conf += 0.15
        confidence = min(0.99, max(0.10, base_conf * authenticity_score * cross_source_agreement))

        # Check for material adverse veto (e.g. verified news sentiment < -0.40)
        has_adverse_veto = bool(verified_sentiments and raw_news_sentiment < -0.35 and confidence >= 0.60)

        # Check for manipulation veto
        is_manipulated = (authenticity_score < 0.50 and tier3_count >= 3)

        if has_adverse_veto:
            model_interp = f"ADVERSE VETOED: Verified negative disclosure ({raw_news_sentiment:+.2f})."
            gate_passed = False
            news_state = "RISK"
        elif is_manipulated:
            model_interp = f"MANIPULATION VETOED: Coordinated bot spam detected (Authenticity: {authenticity_score*100:.0f}%)."
            gate_passed = False
            news_state = "MANIPULATED"
        elif usable_sentiment >= 0.30 and confidence >= 0.65:
            model_interp = f"CATALYST APPROVED: Verified bullish backing (+{usable_sentiment:.2f}, Conf: {confidence*100:.0f}%)."
            gate_passed = True
            news_state = "CATALYST"
        else:
            model_interp = f"NEUTRAL: News flow balanced ({usable_sentiment:+.2f}). Non-blocking for technical setups."
            gate_passed = True
            news_state = "NEUTRAL"

        return {
            "asset": stock_ticker,
            "token_symbol": token_symbol,
            "news_state": news_state,
            "event": "verified_catalyst" if (facts or reports) else "social_buzz",
            "sentiment": round(raw_sentiment, 3),
            "usable_sentiment": round(usable_sentiment, 3),
            "confidence": round(confidence, 3),
            "source_quality": round(source_quality, 3),
            "authenticity_score": round(authenticity_score, 3),
            "cross_source_agreement": round(cross_source_agreement, 3),
            "source_counts": {
                "tier1_primary": tier1_count,
                "tier2_news": tier2_count,
                "tier3_social": tier3_count,
                "total": total_sources
            },
            "facts": facts,
            "reports": reports,
            "social_claims": social_claims[:5],
            "model_interpretation": model_interp,
            "gate_passed": gate_passed,
            "has_adverse_veto": has_adverse_veto,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def detect_news_technical_divergence(self, news_sentiment: float, technical_direction: str) -> Dict[str, Any]:
        """
        Classifies interaction between news sentiment and price trend:
        - NEWS_PRICE_CONFIRMATION
        - NEWS_PRICE_DIVERGENCE
        - NEWS_PRICE_CONTRADICTION
        """
        if abs(news_sentiment) < 0.15 or technical_direction == "NEUTRAL":
            return {"divergence": "NEUTRAL", "description": "No significant news/price disparity"}

        if technical_direction == "LONG":
            if news_sentiment > 0.20:
                return {"divergence": "NEWS_PRICE_CONFIRMATION", "description": "Bullish price confirmed by positive news catalyst"}
            elif news_sentiment < -0.20:
                return {"divergence": "NEWS_PRICE_CONTRADICTION", "description": "Price rising despite negative news. Potential short squeeze or news absorption."}
        elif technical_direction == "SHORT":
            if news_sentiment < -0.20:
                return {"divergence": "NEWS_PRICE_CONFIRMATION", "description": "Bearish price confirmed by negative news catalyst"}
            elif news_sentiment > 0.20:
                return {"divergence": "NEWS_PRICE_CONTRADICTION", "description": "Price dropping despite positive news. Smart money distribution into retail hype."}

        return {"divergence": "NEWS_PRICE_DIVERGENCE", "description": "Mild divergence between price and news flow"}

social_news_agent = SocialNewsAgent()

"""
Screenshot OCR — Extract marketplace listing data from phone screenshots.
Primary: GPT-4o Vision via emergentintegrations
Fallback: Tesseract OCR + regex parsing
"""
import base64
import io
import json
import logging
import os
import re
import uuid
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GPT-4o Vision extraction (primary)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are analyzing a screenshot of a marketplace listing (Facebook Marketplace, Craigslist, OfferUp, etc).

Extract the following fields from the image. Return ONLY valid JSON, no markdown, no explanation:

{
  "title": "item title/name",
  "price": null or number (just the numeric value, no $ sign),
  "location": "city, state or area if visible",
  "description": "any description text visible",
  "seller_name": "seller name if visible",
  "category_hint": "one of: vehicles, equipment, electronics, furniture, other",
  "condition": "new, like new, good, fair, poor, or unknown",
  "image_count": number of product images visible,
  "distance": null or number (miles if shown),
  "listing_source": "facebook marketplace, craigslist, offerup, other, or unknown",
  "urgency_signals": ["list of urgency words like 'must sell', 'moving', 'obo' if present"]
}

If a field is not visible or unclear, use null for numbers/strings or [] for arrays.
Always return valid JSON. Do not include any text outside the JSON object."""


async def extract_with_vision(image_bytes: bytes, mime_type: str = "image/jpeg") -> Optional[dict]:
    """Use GPT-4o to extract listing data from a screenshot."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        logger.warning("EMERGENT_LLM_KEY not set, skipping vision extraction")
        return None

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

        chat = LlmChat(
            api_key=api_key,
            session_id=f"ocr-{uuid.uuid4().hex[:8]}",
            system_message="You are a precise data extraction assistant. You only output valid JSON."
        ).with_model("openai", "gpt-4o")

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        image_content = ImageContent(image_base64=image_b64)

        user_message = UserMessage(
            text=EXTRACTION_PROMPT,
            file_contents=[image_content],
        )

        response = await chat.send_message(user_message)

        # Parse JSON from response
        response_text = response.strip()
        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
            response_text = re.sub(r"\s*```$", "", response_text)

        data = json.loads(response_text)
        logger.info("Vision extraction successful: %s", data.get("title", "?"))
        return data

    except ImportError:
        logger.error("emergentintegrations not available")
        return None
    except json.JSONDecodeError as e:
        logger.error("Vision response was not valid JSON: %s", e)
        return None
    except Exception as e:
        logger.error("Vision extraction failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Tesseract OCR extraction (fallback)
# ---------------------------------------------------------------------------

def extract_with_tesseract(image_bytes: bytes) -> Optional[dict]:
    """Use Tesseract OCR as fallback to extract text from screenshot."""
    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract not available")
        return None

    try:
        image = Image.open(io.BytesIO(image_bytes))

        # Convert to RGB if necessary
        if image.mode != "RGB":
            image = image.convert("RGB")

        # OCR the image
        text = pytesseract.image_to_string(image)

        if not text or len(text.strip()) < 10:
            logger.warning("Tesseract extracted very little text")
            return None

        logger.info("Tesseract extracted %d chars", len(text))

        # Parse the extracted text into a listing
        return parse_ocr_text(text)

    except Exception as e:
        logger.error("Tesseract extraction failed: %s", e)
        return None


def parse_ocr_text(text: str) -> dict:
    """Parse raw OCR text into structured listing data using regex heuristics."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    result = {
        "title": "",
        "price": None,
        "location": "",
        "description": "",
        "seller_name": None,
        "category_hint": "other",
        "condition": "unknown",
        "image_count": 0,
        "distance": None,
        "listing_source": "unknown",
        "urgency_signals": [],
    }

    # Extract price (look for $ amounts)
    for line in lines:
        price_match = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", line)
        if price_match:
            try:
                result["price"] = float(price_match.group(1).replace(",", ""))
            except ValueError:
                pass
            break

    if not result["price"]:
        for line in lines:
            if "free" in line.lower():
                result["price"] = 0.0
                break

    # Extract title (usually the first substantial non-price line)
    for line in lines:
        if "$" not in line and len(line) > 5 and not line.startswith("http"):
            result["title"] = line[:120]
            break

    # Extract location (look for city/state patterns)
    location_pattern = r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*),?\s*([A-Z]{2})"
    for line in lines:
        loc_match = re.search(location_pattern, line)
        if loc_match:
            result["location"] = f"{loc_match.group(1)}, {loc_match.group(2)}"
            break

    # Extract distance
    for line in lines:
        dist_match = re.search(r"([\d.]+)\s*(?:miles?|mi)\s*(?:away)?", line, re.IGNORECASE)
        if dist_match:
            try:
                result["distance"] = float(dist_match.group(1))
            except ValueError:
                pass
            break

    # Build description from remaining text
    desc_parts = []
    for line in lines:
        if line != result["title"] and "$" not in line[:5] and len(line) > 10:
            desc_parts.append(line)
    result["description"] = " ".join(desc_parts[:5])

    # Detect urgency signals
    urgency_keywords = ["must sell", "moving", "obo", "need gone", "asap", "quick sale",
                        "fire sale", "estate sale", "divorce", "emergency", "need cash"]
    text_lower = text.lower()
    result["urgency_signals"] = [kw for kw in urgency_keywords if kw in text_lower]

    # Detect source
    if "facebook" in text_lower or "marketplace" in text_lower:
        result["listing_source"] = "facebook marketplace"
    elif "craigslist" in text_lower:
        result["listing_source"] = "craigslist"
    elif "offerup" in text_lower:
        result["listing_source"] = "offerup"

    return result


# ---------------------------------------------------------------------------
# Combined extraction pipeline
# ---------------------------------------------------------------------------

async def extract_from_screenshot(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    Extract listing data from a screenshot using vision AI (primary) with Tesseract fallback.
    
    Returns a normalized dict ready for process_and_store_listing().
    """
    # Try GPT-4o Vision first
    vision_result = await extract_with_vision(image_bytes, mime_type)

    if vision_result and vision_result.get("title"):
        logger.info("Using vision extraction result")
        return normalize_extraction(vision_result, source="screenshot_vision")

    # Fallback to Tesseract
    logger.info("Falling back to Tesseract OCR")
    tesseract_result = extract_with_tesseract(image_bytes)

    if tesseract_result and tesseract_result.get("title"):
        logger.info("Using Tesseract extraction result")
        return normalize_extraction(tesseract_result, source="screenshot_ocr")

    # Both failed
    return {
        "title": "",
        "price": None,
        "location": "",
        "description": "Failed to extract listing data from screenshot",
        "source": "screenshot_failed",
        "error": "Could not extract listing data. Try a clearer screenshot or import manually.",
    }


def normalize_extraction(data: dict, source: str) -> dict:
    """Normalize extracted data into format expected by process_and_store_listing."""
    return {
        "title": data.get("title", ""),
        "price": data.get("price"),
        "location": data.get("location", ""),
        "description": data.get("description", ""),
        "seller_name": data.get("seller_name"),
        "category": data.get("category_hint"),
        "distance": data.get("distance"),
        "image_count": data.get("image_count", 0),
        "source": source,
        "listing_url": "",
        "image_url": "",
        "keywords": data.get("urgency_signals", []),
    }

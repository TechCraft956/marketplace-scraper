from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


Recommendation = Literal["pursue", "negotiate", "pass"]
SellerType = Literal["private", "dealer", "auction"]
TitleStatus = Literal["clean", "rebuilt", "unknown"]


class VehicleDealInput(BaseModel):
    listing_title: str = Field(..., min_length=3, max_length=200)
    asking_price: float = Field(..., gt=0)
    year: int = Field(..., ge=1980, le=2100)
    make: str = Field(..., min_length=1, max_length=80)
    model: str = Field(..., min_length=1, max_length=80)
    mileage: int = Field(..., ge=0, le=500000)
    apr: float = Field(..., ge=0, le=35)
    loan_term_months: int = Field(..., ge=12, le=96)
    down_payment: float = Field(..., ge=0)
    estimated_taxes_and_fees: float = Field(..., ge=0)
    distance_miles: float = Field(..., ge=0, le=5000)
    estimated_fair_market_value: float = Field(..., gt=0)
    trim: Optional[str] = Field(default=None, max_length=80)
    condition_score: Optional[float] = Field(default=None, ge=1, le=10)
    seller_type: Optional[SellerType] = None
    advertised_monthly_payment: Optional[float] = Field(default=None, ge=0)
    inventory_age_days: Optional[int] = Field(default=None, ge=0, le=3650)
    title_status: Optional[TitleStatus] = None

    @model_validator(mode="after")
    def validate_financing(self) -> "VehicleDealInput":
        if self.down_payment > self.asking_price + self.estimated_taxes_and_fees:
            raise ValueError("down_payment cannot exceed asking price plus estimated taxes and fees")
        return self


class VehicleDealEvaluation(BaseModel):
    rank: int
    listing_title: str
    vehicle_summary: str
    financed_amount: float
    estimated_monthly_payment: float
    total_interest_paid: float
    total_acquisition_cost: float
    market_spread: float
    distance_penalty: float
    deal_score: int
    recommendation: Recommendation
    reason_to_act: str
    top_risks: list[str]


class VehicleDealEvaluationRequest(BaseModel):
    deals: list[VehicleDealInput] = Field(..., min_length=1, max_length=10)


class VehicleDealEvaluationResponse(BaseModel):
    deals: list[VehicleDealEvaluation]
    best_deal: VehicleDealEvaluation


def _round_money(value: float) -> float:
    return round(value, 2)


def _calculate_monthly_payment(financed_amount: float, apr: float, term_months: int) -> float:
    if financed_amount <= 0:
        return 0.0
    monthly_rate = apr / 100 / 12
    if monthly_rate == 0:
        return financed_amount / term_months
    factor = (1 + monthly_rate) ** term_months
    return financed_amount * (monthly_rate * factor) / (factor - 1)


def evaluate_vehicle_deals(payload: VehicleDealEvaluationRequest) -> VehicleDealEvaluationResponse:
    evaluations: list[VehicleDealEvaluation] = []

    for deal in payload.deals:
        financed_amount = max(deal.asking_price + deal.estimated_taxes_and_fees - deal.down_payment, 0.0)
        estimated_monthly_payment = _calculate_monthly_payment(financed_amount, deal.apr, deal.loan_term_months)
        total_of_payments = estimated_monthly_payment * deal.loan_term_months
        total_interest_paid = max(total_of_payments - financed_amount, 0.0)
        total_acquisition_cost = deal.down_payment + deal.estimated_taxes_and_fees + total_of_payments
        market_spread = deal.estimated_fair_market_value - total_acquisition_cost

        distance_penalty = min(deal.distance_miles * 0.18, 25.0)
        mileage_penalty = min(max(deal.mileage - 60000, 0) / 5000, 18.0)
        apr_penalty = min(max(deal.apr - 4.0, 0) * 1.8, 22.0)
        term_penalty = max(deal.loan_term_months - 60, 0) * 0.35
        fee_penalty = min((deal.estimated_taxes_and_fees / max(deal.asking_price, 1)) * 100, 12.0)
        payment_penalty = min((estimated_monthly_payment / max(deal.estimated_fair_market_value / 60, 1)) * 3.5, 18.0)

        seller_penalty = {"private": 0.0, "dealer": 4.0, "auction": 7.0}.get(deal.seller_type or "private", 0.0)
        title_penalty = {"clean": 0.0, "unknown": 5.0, "rebuilt": 12.0}.get(deal.title_status or "unknown", 5.0 if not deal.title_status else 0.0)
        condition_bonus = ((deal.condition_score or 6.0) - 6.0) * 2.0
        inventory_bonus = 4.0 if (deal.inventory_age_days or 0) >= 45 else 0.0
        value_bonus = max(min((market_spread / deal.estimated_fair_market_value) * 100, 30.0), -35.0)

        raw_score = 75 + value_bonus + condition_bonus + inventory_bonus - (
            distance_penalty + mileage_penalty + apr_penalty + term_penalty + fee_penalty + payment_penalty + seller_penalty + title_penalty
        )
        deal_score = max(0, min(100, round(raw_score)))

        risks: list[str] = []
        if deal.apr >= 9:
            risks.append("High APR increases financing cost")
        if deal.loan_term_months >= 72:
            risks.append("Long loan term keeps you in debt longer")
        if deal.distance_miles >= 100:
            risks.append("Distance adds travel friction and inspection risk")
        if deal.mileage >= 100000:
            risks.append("Higher mileage raises maintenance uncertainty")
        if deal.estimated_taxes_and_fees >= deal.asking_price * 0.1:
            risks.append("Taxes and fees are heavy relative to price")
        if (deal.title_status or "unknown") != "clean":
            risks.append(f"Title status is {(deal.title_status or 'unknown')}")
        if market_spread < 0:
            risks.append("Total acquisition cost sits above estimated fair value")
        if not risks:
            risks.append("No major structural risk flags from the provided inputs")

        if deal_score >= 78 and market_spread >= -(deal.estimated_fair_market_value * 0.03):
            recommendation: Recommendation = "pursue"
        elif deal_score >= 40 and market_spread >= -(deal.estimated_fair_market_value * 0.12):
            recommendation = "negotiate"
        else:
            recommendation = "pass"

        positives: list[str] = []
        if market_spread > 0:
            positives.append("lands below estimated market value after financing")
        if deal.distance_miles <= 50:
            positives.append("has manageable distance")
        if deal.apr <= 6:
            positives.append("uses a reasonable APR")
        if (deal.condition_score or 6) >= 8:
            positives.append("shows strong reported condition")
        if not positives:
            positives.append("could improve only with better price or financing")

        reason_to_act = f"{recommendation.title()} because it {', '.join(positives[:3])}."
        vehicle_summary = " ".join(str(part) for part in [deal.year, deal.make, deal.model, deal.trim] if part)

        evaluations.append(
            VehicleDealEvaluation(
                rank=0,
                listing_title=deal.listing_title,
                vehicle_summary=vehicle_summary,
                financed_amount=_round_money(financed_amount),
                estimated_monthly_payment=_round_money(estimated_monthly_payment),
                total_interest_paid=_round_money(total_interest_paid),
                total_acquisition_cost=_round_money(total_acquisition_cost),
                market_spread=_round_money(market_spread),
                distance_penalty=round(distance_penalty, 2),
                deal_score=deal_score,
                recommendation=recommendation,
                reason_to_act=reason_to_act,
                top_risks=risks[:3],
            )
        )

    evaluations.sort(key=lambda item: (item.deal_score, item.market_spread), reverse=True)
    ranked = [item.model_copy(update={"rank": index}) for index, item in enumerate(evaluations, start=1)]
    return VehicleDealEvaluationResponse(deals=ranked, best_deal=ranked[0])

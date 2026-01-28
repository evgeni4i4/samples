#   Copyright 2026 Ivinco Ltd - ACP Protocol Adapter
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""ACP (Agentic Commerce Protocol) Pydantic models.

This module defines request/response models for the ACP protocol endpoints,
following the OpenAPI specification from agentic-commerce-protocol/agentic-commerce-protocol.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field


# --- ACP Item Models ---

class ACPItem(BaseModel):
    """An item in an ACP checkout."""
    sku: str = Field(..., description="Product SKU identifier")
    quantity: int = Field(1, ge=1, description="Quantity of item")
    price: int | None = Field(None, description="Price in smallest currency unit")
    name: str | None = Field(None, description="Product name/title")


class ACPLineItem(BaseModel):
    """Line item in ACP checkout response."""
    id: str
    sku: str
    name: str
    quantity: int
    unit_price: int
    total_price: int


# --- ACP Buyer Models ---

class ACPBuyer(BaseModel):
    """Buyer information for ACP checkout."""
    email: str | None = None
    name: str | None = None
    phone: str | None = None


# --- ACP Fulfillment Models ---

class ACPAddress(BaseModel):
    """Shipping address for ACP."""
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class ACPFulfillmentOption(BaseModel):
    """A fulfillment option."""
    id: str
    name: str
    price: int
    estimated_delivery: str | None = None


class ACPFulfillmentDetails(BaseModel):
    """Fulfillment details for ACP checkout."""
    shipping_address: ACPAddress | None = None
    selected_option_id: str | None = None


# --- ACP Payment Models ---

class ACPPaymentData(BaseModel):
    """Payment data for completing checkout."""
    token: str = Field(..., description="SharedPaymentToken from PSP")
    provider: str = Field(..., description="Payment provider (e.g., 'stripe')")


class ACPPaymentOption(BaseModel):
    """A payment option."""
    id: str
    type: str
    provider: str


# --- ACP Affiliate Attribution ---

class ACPAffiliateAttribution(BaseModel):
    """Affiliate attribution for tracking."""
    source: str | None = None
    campaign: str | None = None
    medium: str | None = None


# --- ACP Intent Trace ---

class ACPIntentTrace(BaseModel):
    """Intent trace for cancellation."""
    reason_code: str | None = None


# --- ACP Request Models ---

class ACPCheckoutSessionCreateRequest(BaseModel):
    """Request to create an ACP checkout session."""
    items: list[ACPItem] = Field(..., min_length=1)
    buyer: ACPBuyer | None = None
    fulfillment_details: ACPFulfillmentDetails | None = None
    affiliate_attribution: ACPAffiliateAttribution | None = None


class ACPCheckoutSessionUpdateRequest(BaseModel):
    """Request to update an ACP checkout session."""
    items: list[ACPItem] | None = None
    buyer: ACPBuyer | None = None
    fulfillment_details: ACPFulfillmentDetails | None = None
    selected_fulfillment_options: list[str] | None = None


class ACPCheckoutSessionCompleteRequest(BaseModel):
    """Request to complete an ACP checkout session."""
    payment_data: ACPPaymentData
    buyer: ACPBuyer | None = None
    affiliate_attribution: ACPAffiliateAttribution | None = None


class ACPCancelSessionRequest(BaseModel):
    """Request to cancel an ACP checkout session."""
    intent_trace: ACPIntentTrace | None = None


# --- ACP Response Models ---

class ACPOrder(BaseModel):
    """Order created after checkout completion."""
    id: str
    status: str
    created_at: str | None = None


class ACPCheckoutSession(BaseModel):
    """ACP Checkout Session response."""
    id: str
    status: Literal["open", "complete", "canceled", "expired"]
    items: list[ACPLineItem]
    subtotal: int
    total: int
    currency: str = "USD"
    buyer: ACPBuyer | None = None
    fulfillment_details: ACPFulfillmentDetails | None = None
    fulfillment_options: list[ACPFulfillmentOption] | None = None
    payment_options: list[ACPPaymentOption] | None = None
    shipping_cost: int | None = None
    tax: int | None = None
    discount: int | None = None
    metadata: dict[str, Any] | None = None


class ACPCheckoutSessionWithOrder(ACPCheckoutSession):
    """ACP Checkout Session response with order (after completion)."""
    order: ACPOrder | None = None

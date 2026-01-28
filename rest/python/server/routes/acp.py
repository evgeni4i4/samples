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

"""ACP (Agentic Commerce Protocol) routes.

This module implements the ACP endpoints that translate to the internal
checkout service, enabling dual-protocol support (UCP + ACP).

Endpoints:
- POST /checkout_sessions - Create checkout
- GET /checkout_sessions/{id} - Retrieve checkout
- POST /checkout_sessions/{id} - Update checkout
- POST /checkout_sessions/{id}/complete - Complete checkout
- POST /checkout_sessions/{id}/cancel - Cancel checkout
"""

import datetime
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dependencies import get_checkout_service
from models_acp import (
    ACPBuyer,
    ACPCancelSessionRequest,
    ACPCheckoutSession,
    ACPCheckoutSessionCompleteRequest,
    ACPCheckoutSessionCreateRequest,
    ACPCheckoutSessionUpdateRequest,
    ACPCheckoutSessionWithOrder,
    ACPFulfillmentOption,
    ACPLineItem,
    ACPOrder,
    ACPPaymentOption,
)
from services.checkout_service import CheckoutService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/acp", tags=["ACP - Agentic Commerce Protocol"])

# Bearer token auth
security = HTTPBearer(auto_error=False)


async def verify_acp_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Verify ACP Bearer token authentication.

    In production, this would validate against a real auth system.
    For demo purposes, we accept any Bearer token.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # For demo, accept any token. Production would validate against issuer.
    return credentials.credentials


def _map_ucp_status_to_acp(ucp_status: str) -> str:
    """Map UCP checkout status to ACP status."""
    mapping = {
        "in_progress": "open",
        "ready_for_complete": "open",
        "completed": "complete",
        "canceled": "canceled",
    }
    return mapping.get(ucp_status.lower(), "open")


def _ucp_checkout_to_acp_session(
    ucp_checkout: dict,
    fulfillment_options: list[ACPFulfillmentOption] | None = None,
) -> ACPCheckoutSession:
    """Convert a UCP checkout response to ACP checkout session format."""

    # Map line items
    acp_items = []
    for li in ucp_checkout.get("line_items", []):
        item = li.get("item", {})
        quantity = li.get("quantity", 1)
        price = item.get("price", 0)
        acp_items.append(
            ACPLineItem(
                id=li.get("id", str(uuid.uuid4())),
                sku=item.get("id", ""),
                name=item.get("title", "Unknown"),
                quantity=quantity,
                unit_price=price,
                total_price=price * quantity,
            )
        )

    # Calculate totals
    totals = ucp_checkout.get("totals", [])
    subtotal = next((t["amount"] for t in totals if t["type"] == "subtotal"), 0)
    total = next((t["amount"] for t in totals if t["type"] == "total"), subtotal)
    shipping = next((t["amount"] for t in totals if t["type"] == "fulfillment"), None)
    discount = next((t["amount"] for t in totals if t["type"] == "discount"), None)

    # Map buyer
    ucp_buyer = ucp_checkout.get("buyer")
    acp_buyer = None
    if ucp_buyer:
        acp_buyer = ACPBuyer(
            email=ucp_buyer.get("email"),
            name=ucp_buyer.get("name"),
            phone=ucp_buyer.get("phone"),
        )

    # Standard payment options
    payment_options = [
        ACPPaymentOption(id="stripe", type="card", provider="stripe"),
        ACPPaymentOption(id="paypal", type="wallet", provider="paypal"),
    ]

    return ACPCheckoutSession(
        id=ucp_checkout.get("id", ""),
        status=_map_ucp_status_to_acp(ucp_checkout.get("status", "in_progress")),
        items=acp_items,
        subtotal=subtotal,
        total=total,
        currency=ucp_checkout.get("currency", "USD"),
        buyer=acp_buyer,
        fulfillment_options=fulfillment_options,
        payment_options=payment_options,
        shipping_cost=shipping,
        discount=discount,
    )


@router.post(
    "/checkout_sessions",
    response_model=ACPCheckoutSession,
    status_code=status.HTTP_201_CREATED,
    summary="Create Checkout Session",
    description="Create a new ACP checkout session with items.",
)
async def create_checkout_session(
    request: Request,
    body: ACPCheckoutSessionCreateRequest,
    checkout_service: CheckoutService = Depends(get_checkout_service),
    api_version: str = Header(default="2026-01-16", alias="API-Version"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_acp_auth),
):
    """Create an ACP checkout session.

    Translates ACP request to internal UCP format and creates checkout.
    """
    logger.info("ACP: Creating checkout session with %d items", len(body.items))

    # Import here to avoid circular imports
    from models import UnifiedCheckoutCreateRequest
    from ucp_sdk.models.schemas.shopping.types import item_create_req
    from ucp_sdk.models.schemas.shopping.types import line_item_create_req
    from ucp_sdk.models.schemas.shopping.payment_create_req import PaymentCreateRequest

    # Convert ACP items to UCP line items
    line_items = []
    for acp_item in body.items:
        line_items.append(
            line_item_create_req.LineItemCreateRequest(
                item=item_create_req.ItemCreateRequest(
                    id=acp_item.sku, title=acp_item.name or ""
                ),
                quantity=acp_item.quantity,
            )
        )

    # Create UCP checkout request
    ucp_request = UnifiedCheckoutCreateRequest(
        currency="USD",
        line_items=line_items,
        payment=PaymentCreateRequest(),
    )

    # Add buyer if provided
    if body.buyer:
        from ucp_sdk.models.schemas.shopping.types.buyer import Buyer
        ucp_request.buyer = Buyer(
            email=body.buyer.email,
            name=body.buyer.name,
        )

    # Generate idempotency key if not provided
    idem_key = idempotency_key or f"acp_{uuid.uuid4()}"

    try:
        checkout = await checkout_service.create_checkout(ucp_request, idem_key)
        checkout_dict = checkout.model_dump(mode="json", by_alias=True)

        # Generate fulfillment options
        fulfillment_options = [
            ACPFulfillmentOption(
                id="standard",
                name="Standard Shipping",
                price=999,
                estimated_delivery="5-7 business days",
            ),
            ACPFulfillmentOption(
                id="express",
                name="Express Shipping",
                price=1999,
                estimated_delivery="2-3 business days",
            ),
        ]

        return _ucp_checkout_to_acp_session(checkout_dict, fulfillment_options)
    except Exception as e:
        logger.error("ACP create checkout failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/checkout_sessions/{checkout_session_id}",
    response_model=ACPCheckoutSession,
    summary="Retrieve Checkout Session",
    description="Retrieve an existing ACP checkout session by ID.",
)
async def get_checkout_session(
    checkout_session_id: str,
    checkout_service: CheckoutService = Depends(get_checkout_service),
    api_version: str = Header(default="2026-01-16", alias="API-Version"),
    _token: str = Depends(verify_acp_auth),
):
    """Retrieve an ACP checkout session."""
    logger.info("ACP: Getting checkout session %s", checkout_session_id)

    try:
        checkout = await checkout_service.get_checkout(checkout_session_id)
        checkout_dict = checkout.model_dump(mode="json", by_alias=True)
        return _ucp_checkout_to_acp_session(checkout_dict)
    except Exception as e:
        logger.error("ACP get checkout failed: %s", e)
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Checkout session not found")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/checkout_sessions/{checkout_session_id}",
    response_model=ACPCheckoutSession,
    summary="Update Checkout Session",
    description="Update an existing ACP checkout session.",
)
async def update_checkout_session(
    checkout_session_id: str,
    body: ACPCheckoutSessionUpdateRequest,
    checkout_service: CheckoutService = Depends(get_checkout_service),
    api_version: str = Header(default="2026-01-16", alias="API-Version"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_acp_auth),
):
    """Update an ACP checkout session."""
    logger.info("ACP: Updating checkout session %s", checkout_session_id)

    from models import UnifiedCheckoutUpdateRequest
    from ucp_sdk.models.schemas.shopping.types import item_update_req
    from ucp_sdk.models.schemas.shopping.types import line_item_update_req
    from ucp_sdk.models.schemas.shopping.payment_update_req import PaymentUpdateRequest

    # First, get the existing checkout to merge updates
    try:
        existing_checkout = await checkout_service.get_checkout(checkout_session_id)
        existing_dict = existing_checkout.model_dump(mode="json")
    except Exception:
        raise HTTPException(status_code=404, detail="Checkout session not found")

    # Build line items for update - merge with existing if not provided
    line_items = []
    if body.items:
        for acp_item in body.items:
            line_items.append(
                line_item_update_req.LineItemUpdateRequest(
                    item=item_update_req.ItemUpdateRequest(
                        id=acp_item.sku, title=acp_item.name or ""
                    ),
                    quantity=acp_item.quantity,
                )
            )
    else:
        # Keep existing line items
        for li in existing_dict.get("line_items", []):
            item = li.get("item", {})
            line_items.append(
                line_item_update_req.LineItemUpdateRequest(
                    id=li.get("id"),
                    item=item_update_req.ItemUpdateRequest(
                        id=item.get("id"), title=item.get("title", "")
                    ),
                    quantity=li.get("quantity", 1),
                )
            )

    # Handle buyer update
    buyer = None
    if body.buyer:
        from ucp_sdk.models.schemas.shopping.types.buyer import Buyer
        buyer = Buyer(
            email=body.buyer.email,
            name=body.buyer.name,
        )
    elif existing_dict.get("buyer"):
        from ucp_sdk.models.schemas.shopping.types.buyer import Buyer
        buyer = Buyer(**existing_dict["buyer"])

    ucp_request = UnifiedCheckoutUpdateRequest(
        id=checkout_session_id,
        line_items=line_items,
        currency=existing_dict.get("currency", "USD"),
        payment=PaymentUpdateRequest(),
        buyer=buyer,
    )
    idem_key = idempotency_key or f"acp_upd_{uuid.uuid4()}"

    try:
        checkout = await checkout_service.update_checkout(
            checkout_session_id, ucp_request, idem_key
        )
        checkout_dict = checkout.model_dump(mode="json", by_alias=True)
        return _ucp_checkout_to_acp_session(checkout_dict)
    except Exception as e:
        logger.error("ACP update checkout failed: %s", e)
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Checkout session not found")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/checkout_sessions/{checkout_session_id}/complete",
    response_model=ACPCheckoutSessionWithOrder,
    summary="Complete Checkout Session",
    description="Complete checkout with SharedPaymentToken.",
)
async def complete_checkout_session(
    checkout_session_id: str,
    body: ACPCheckoutSessionCompleteRequest,
    checkout_service: CheckoutService = Depends(get_checkout_service),
    api_version: str = Header(default="2026-01-16", alias="API-Version"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_acp_auth),
):
    """Complete an ACP checkout session with payment.

    This endpoint accepts a SharedPaymentToken from the payment provider
    (typically Stripe) and completes the transaction.
    """
    logger.info("ACP: Completing checkout session %s", checkout_session_id)

    from ucp_sdk.models.schemas.shopping.payment_create_req import PaymentCreateRequest
    from ucp_sdk.models.schemas.shopping.types import payment_instrument

    # Map ACP payment to UCP format
    instrument_id = str(uuid.uuid4())

    # Create payment instrument with the SharedPaymentToken
    payment_request = PaymentCreateRequest(
        selected_instrument_id=instrument_id,
        instruments=[
            payment_instrument.PaymentInstrument(
                root=payment_instrument.TokenPaymentInstrument(
                    id=instrument_id,
                    handler_id="mock_payment_handler",  # For demo
                    credential={"token": "success_token"},  # Mock success
                )
            )
        ],
    )

    idem_key = idempotency_key or f"acp_complete_{uuid.uuid4()}"

    try:
        checkout = await checkout_service.complete_checkout(
            checkout_session_id,
            payment_request,
            risk_signals={},
            idempotency_key=idem_key,
        )
        checkout_dict = checkout.model_dump(mode="json", by_alias=True)

        # Build response with order
        acp_session = _ucp_checkout_to_acp_session(checkout_dict)

        # Add order info
        order_info = checkout_dict.get("order", {})
        order = ACPOrder(
            id=order_info.get("id", str(uuid.uuid4())),
            status="confirmed",
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

        return ACPCheckoutSessionWithOrder(
            **acp_session.model_dump(),
            order=order,
        )
    except Exception as e:
        logger.error("ACP complete checkout failed: %s", e)
        error_msg = str(e).lower()
        if "not found" in error_msg:
            raise HTTPException(status_code=404, detail="Checkout session not found")
        if "not modifiable" in error_msg:
            raise HTTPException(status_code=409, detail="Checkout already completed")
        if "fulfillment" in error_msg:
            raise HTTPException(
                status_code=400,
                detail="Fulfillment address and option must be selected"
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/checkout_sessions/{checkout_session_id}/cancel",
    response_model=ACPCheckoutSession,
    summary="Cancel Checkout Session",
    description="Cancel an existing checkout session.",
)
async def cancel_checkout_session(
    checkout_session_id: str,
    body: ACPCancelSessionRequest | None = None,
    checkout_service: CheckoutService = Depends(get_checkout_service),
    api_version: str = Header(default="2026-01-16", alias="API-Version"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_acp_auth),
):
    """Cancel an ACP checkout session."""
    logger.info("ACP: Canceling checkout session %s", checkout_session_id)

    idem_key = idempotency_key or f"acp_cancel_{uuid.uuid4()}"

    try:
        checkout = await checkout_service.cancel_checkout(
            checkout_session_id, idem_key
        )
        checkout_dict = checkout.model_dump(mode="json", by_alias=True)
        return _ucp_checkout_to_acp_session(checkout_dict)
    except Exception as e:
        logger.error("ACP cancel checkout failed: %s", e)
        error_msg = str(e).lower()
        if "not found" in error_msg:
            raise HTTPException(status_code=404, detail="Checkout session not found")
        if "not modifiable" in error_msg or "cannot cancel" in error_msg:
            raise HTTPException(
                status_code=405,
                detail="Checkout session cannot be canceled (already completed or canceled)",
            )
        raise HTTPException(status_code=500, detail=str(e))


# --- ACP Discovery Endpoint ---

@router.get(
    "/.well-known/acp",
    summary="ACP Discovery",
    description="Returns ACP capabilities and endpoints.",
)
async def acp_discovery(request: Request):
    """Return ACP discovery document."""
    base_url = str(request.base_url).rstrip("/")

    return {
        "protocol": "acp",
        "version": "2026-01-16",
        "merchant": {
            "name": "Ivinco Demo Store",
            "support_email": "support@ivinco.com",
        },
        "endpoints": {
            "create_checkout": f"{base_url}/acp/checkout_sessions",
            "retrieve_checkout": f"{base_url}/acp/checkout_sessions/{{checkout_session_id}}",
            "update_checkout": f"{base_url}/acp/checkout_sessions/{{checkout_session_id}}",
            "complete_checkout": f"{base_url}/acp/checkout_sessions/{{checkout_session_id}}/complete",
            "cancel_checkout": f"{base_url}/acp/checkout_sessions/{{checkout_session_id}}/cancel",
        },
        "authentication": {
            "type": "bearer",
            "header": "Authorization",
        },
        "payment_providers": ["stripe", "paypal"],
        "fulfillment_options": ["standard", "express"],
    }

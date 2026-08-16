"""Compatibility surface for Kurv's member-pricing classifier.

The implementation lives in member_pricing_v3 so the old module path remains
stable for callers, tests and future migrations.
"""

from .member_pricing_v3 import MemberPricing, detect_member_pricing, has_membership_signal

__all__ = ["MemberPricing", "detect_member_pricing", "has_membership_signal"]

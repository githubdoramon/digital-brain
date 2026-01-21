"""
Emergency Stock Agent

Checks a Google Sheet for emergency stock levels and expiry dates,
then creates TODOs to consume items or restock.
"""

from agents.emergency_stock.executor import handle_emergency_stock_request

__all__ = ["handle_emergency_stock_request"]

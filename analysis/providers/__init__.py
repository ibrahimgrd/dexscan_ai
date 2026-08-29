"""Concrete MarketDataProvider/SecurityDataProvider/etc. adapters (Part
II.3). Each file here owns exactly one external API's specific field
names and error shapes, translating them into this project's normalized
dataclasses (analysis/api_abstraction.py) - nothing outside this package
should ever need to know a provider's actual JSON schema.
"""

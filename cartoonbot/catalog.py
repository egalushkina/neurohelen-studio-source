"""Token packages and generation prices shared by UI and payment invoices."""
from decimal import Decimal, ROUND_HALF_UP
# Display-only prices; USD equivalents use a planning rate of 90 RUB/USD.
PACKAGES = (
    ('trial', 'package_trial', 1000, 760),
    ('standard', 'package_standard', 2700, 2490),
    ('creator', 'package_creator', 5600, 4990),
    ('studio', 'package_studio', 10500, 8990),
)


def number(value):
    return f'{value:,}'.replace(',', ' ')

# Preliminary per-video token prices, 15-second output at 720p.
SEEDANCE_TOKENS = {'2.5': 700, '2.0': 550, '2.0-fast': 450}


def has_video_reference(data):
    """Seedance never receives source videos; uploads are analysis inputs only."""
    return False


def seedance_tokens(data, version, overrides):
    key = 'seedance_' + version
    if version=='2.5' and data.get('seconds',15)==30:
        return overrides.get('seedance_2.5_30',1300)
    return overrides.get(key, SEEDANCE_TOKENS[version]) * (2 if data.get('seconds',15)==30 else 1)


def omni_tokens(overrides):
    return overrides.get('omni', 200)


def video_quote(data, overrides):
    if data['route']!='omni':
        return seedance_tokens(data,data['version'],overrides),data['seconds']
    selected=data.get('resume_scene_ids') or [x['id'] for x in data.get('video_edits',[])]
    ids=[x['id'] for x in data.get('script',{}).get('scenes',[])
         if (x['id'] in selected if selected else str(x['id']) not in data.get('videos',{})
             and x['id'] not in data.get('skipped_scenes',[]))]
    return len(ids)*omni_tokens(overrides),len(ids)*10


def usd_equivalent(rubles):
    return f'{rubles / 90:.2f}'


def price_guide(lang, overrides):
    from .i18n import tr
    return tr(lang, 'generation_price_guide', omni=omni_tokens(overrides),
              fast=seedance_tokens({}, '2.0-fast', overrides),
              seed=seedance_tokens({}, '2.0', overrides),
              premium=seedance_tokens({}, '2.5', overrides),
              long=seedance_tokens({'seconds':30}, '2.5', overrides))


def package_usd(key, rubles):
    return "9.00" if key == "trial" else usd_equivalent(rubles)

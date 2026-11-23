import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=ROOT / '.env'):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    root: Path = ROOT / 'runtime'
    mode: str = 'demo'
    token: str = ''
    payment_provider_token: str = field(default='', repr=False)
    stripe_provider_token: str = field(default='', repr=False)
    stripe_secret_key: str = field(default='', repr=False)
    stripe_webhook_secret: str = field(default='', repr=False)
    payment_receipts: str = ''
    payment_vat_code: int = 0
    admins: set = field(default_factory=set)
    workers: int = 2
    max_seconds: int = 60
    allow_live: bool = False
    public_access: bool = False
    billing_enabled: bool = False
    require_purchase: bool = False
    token_prices: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls):
        load_env()
        mode = os.getenv('GENERATION_MODE', 'demo')
        if mode not in ('demo', 'live', 'codex'):
            raise ValueError('GENERATION_MODE must be demo, live or codex')
        return cls(mode=mode, token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
                   payment_provider_token=os.getenv('YOOKASSA_PROVIDER_TOKEN',''),
                   stripe_provider_token=os.getenv('STRIPE_PROVIDER_TOKEN',''),
                   stripe_secret_key=os.getenv('STRIPE_SECRET_KEY',''),
                   stripe_webhook_secret=os.getenv('STRIPE_WEBHOOK_SECRET',''),
                   payment_receipts=os.getenv('YOOKASSA_RECEIPTS',''),
                   payment_vat_code=int(os.getenv('YOOKASSA_VAT_CODE','0')),
                   admins={int(x) for x in os.getenv('ADMIN_USER_IDS', '').split(',') if x.strip()},
                   workers=max(1, min(4, int(os.getenv('WORKERS', '2')))),
                   max_seconds=max(10, min(60, int(os.getenv('MAX_PROJECT_SECONDS', '60')))),
                   public_access=os.getenv('PUBLIC_ACCESS') == 'true',
                   billing_enabled=os.getenv('TOKEN_BILLING_ENABLED') == 'true',
                   require_purchase=os.getenv('REQUIRE_TOKEN_PURCHASE') == 'true',
                   allow_live=os.getenv('ALLOW_OWNER_LIVE_GENERATION') == 'true',
                   token_prices={model:int(os.environ[key]) for model,key in (
                       ('omni','OMNI_10S_PRICE_TOKENS'),('seedance_2.0','SEEDANCE_20_15S_PRICE_TOKENS'),
                       ('seedance_2.5','SEEDANCE_25_15S_PRICE_TOKENS'),('seedance_2.5_30','SEEDANCE_25_30S_PRICE_TOKENS'),('seedance_2.0-fast','SEEDANCE_20_FAST_15S_PRICE_TOKENS')) if os.getenv(key)})

    def prepare(self):
        if any(type(v) is not int or v<=0 for v in self.token_prices.values()):
            raise ValueError('Configured token prices must be positive integers')
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'media').mkdir(exist_ok=True)
        if self.mode == 'codex' and not self.admins:
            raise ValueError('Codex mode requires ADMIN_USER_IDS for a private owner pilot')
        if self.mode == 'live':
            if not self.allow_live or not self.admins:
                raise ValueError('Live mode requires owner IDs and explicit ALLOW_OWNER_LIVE_GENERATION=true')
            from .omni_vertex import configured as omni_configured
            if not omni_configured():
                raise ValueError('Missing Omni credentials')
            for key in ('OPENAI_API_KEY', 'OPENAI_TEXT_MODEL', 'OPENAI_IMAGE_MODEL', 'BYTEPLUS_API_KEY'):
                if not os.getenv(key):
                    raise ValueError('Missing setting: ' + key)

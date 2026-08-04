"""Load and validate the brand-set configuration.

Two ways in: a YAML file (``config/brands.yaml``) or ad-hoc CLI flags. Both
produce the same :class:`Config` object, so nothing downstream needs to know
which was used.

Validation failures raise :class:`ConfigError` with a message that names the
offending field. A user who mistypes a key should get a sentence, not a
traceback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG_PATH = Path("config/brands.yaml")
DEFAULT_SMOOTHING_WINDOWS = [3, 12]

# A few common DataForSEO location codes, used only to make error messages and
# `sos init` friendlier. The full list is at:
# https://api.dataforseo.com/v3/keywords_data/google_ads/locations
COMMON_LOCATIONS = {
    "US": 2840,
    "UK": 2826,
    "GB": 2826,
    "CA": 2124,
    "AU": 2036,
    "DE": 2276,
    "FR": 2250,
    "ES": 2724,
    "IT": 2380,
    "NL": 2528,
    "IE": 2372,
    "NZ": 2554,
    "IN": 2356,
    "JP": 2392,
    "BR": 2076,
}

# Env var holding the DataForSEO login. DATAFORSEO_USERNAME is accepted as an
# alias because some other tools use that name; DATAFORSEO_LOGIN wins.
LOGIN_ENV_VARS = ("DATAFORSEO_LOGIN", "DATAFORSEO_USERNAME")
PASSWORD_ENV_VAR = "DATAFORSEO_PASSWORD"


class ConfigError(Exception):
    """Configuration is missing, malformed, or internally inconsistent."""


@dataclass
class Brand:
    """One brand in the category set."""

    name: str
    keywords: List[str]
    is_own_brand: bool = False
    url: Optional[str] = None
    ambiguous: bool = False


@dataclass
class Market:
    """The geography and language the volumes are measured in."""

    name: str
    location_code: int
    language_code: str


@dataclass
class Config:
    """A validated brand set, ready to fetch."""

    market: Market
    brands: List[Brand]
    smoothing_windows: List[int] = field(default_factory=lambda: list(DEFAULT_SMOOTHING_WINDOWS))
    source_path: Optional[Path] = None

    @property
    def own_brand(self) -> Brand:
        return next(b for b in self.brands if b.is_own_brand)

    @property
    def competitors(self) -> List[Brand]:
        return [b for b in self.brands if not b.is_own_brand]

    @property
    def all_keywords(self) -> List[str]:
        """Every keyword across every brand, deduplicated, order preserved.

        These go out in a single API request — DataForSEO bills per request,
        not per keyword.
        """
        seen: Dict[str, None] = {}
        for brand in self.brands:
            for keyword in brand.keywords:
                seen.setdefault(keyword, None)
        return list(seen)

    def brand_for_keyword(self, keyword: str) -> Optional[str]:
        """Map a keyword back to its brand name (first match wins)."""
        for brand in self.brands:
            if keyword in brand.keywords:
                return brand.name
        return None

    @property
    def ambiguous_brands(self) -> List[str]:
        return [b.name for b in self.brands if b.ambiguous]


#: A brand needs this many times fewer keywords than the best-covered brand
#: before the imbalance is worth mentioning. Two-versus-one is ordinary; a
#: threefold gap usually means someone stopped typing.
KEYWORD_PARITY_RATIO = 3


def keyword_parity_warnings(keywords_by_brand: Dict[str, List[str]]) -> List[str]:
    """Flag brands covered by far fewer keywords than the best-covered one.

    Share is a ratio between brands, so keyword depth has to be comparable
    across them. A brand tracked on its name alone, competing against one
    tracked on its name plus five products, will show a smaller share than it
    actually has — and nothing else in the output reveals why. This is advice,
    never an error: plenty of brands genuinely have one search term.
    """
    counts = {name: len(set(kws)) for name, kws in keywords_by_brand.items() if kws}
    if len(counts) < 2:
        return []

    best_name, best = max(counts.items(), key=lambda item: item[1])
    thin = sorted(
        name for name, count in counts.items() if count * KEYWORD_PARITY_RATIO <= best
    )
    if not thin:
        return []

    return [
        f"Uneven keyword coverage: {', '.join(thin)} "
        f"{'is' if len(thin) == 1 else 'are'} tracked on far fewer keywords than "
        f"{best_name} ({best}). Share is a ratio between brands, so a thinly covered "
        "brand looks smaller than it is. Add their sub-brands and product names — "
        "extra keywords cost nothing, since billing is per request."
    ]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_config(path: Optional[Path] = None) -> Config:
    """Read and validate a brands YAML file."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not path.exists():
        raise ConfigError(
            f"No config file at '{path}'.\n"
            "  Create one with:  sos init\n"
            "  Or copy the template:  cp config/brands.example.yaml config/brands.yaml\n"
            "  Or skip config entirely:  sos run --brand Acme --competitors \"Globex,Initech\""
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"'{path}' is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"'{path}' must contain a YAML mapping at the top level.")

    config = _config_from_dict(raw)
    config.source_path = path
    return config


def config_from_flags(
    brand: str,
    competitors: List[str],
    brand_url: Optional[str] = None,
    market_name: str = "US",
    location_code: Optional[int] = None,
    language_code: str = "en",
    smoothing_windows: Optional[List[int]] = None,
) -> Config:
    """Build a config from ad-hoc CLI flags, with no file involved.

    Brand names double as their own single keyword — good enough for a quick
    look, though a real run usually wants keyword variants in a config file.
    """
    if not brand or not brand.strip():
        raise ConfigError("--brand cannot be empty.")

    cleaned_competitors = [c.strip() for c in competitors if c and c.strip()]
    if not cleaned_competitors:
        raise ConfigError(
            "Ad-hoc mode needs at least one competitor: "
            '--competitors "Globex,Initech".\n'
            "Share of Search is a ratio against a category — a brand on its own is always 100%."
        )

    if location_code is None:
        resolved = COMMON_LOCATIONS.get(market_name.strip().upper())
        if resolved is None:
            raise ConfigError(
                f"Don't know the DataForSEO location code for market '{market_name}'.\n"
                f"  Known shorthands: {', '.join(sorted(COMMON_LOCATIONS))}\n"
                "  For anywhere else, use a config file and set market.location_code explicitly."
            )
        location_code = resolved

    brands = [Brand(name=brand.strip(), keywords=[brand.strip().lower()], is_own_brand=True, url=brand_url)]
    brands += [Brand(name=c, keywords=[c.lower()]) for c in cleaned_competitors]

    config = Config(
        market=Market(
            name=market_name.strip().upper(),
            location_code=int(location_code),
            language_code=language_code,
        ),
        brands=brands,
        smoothing_windows=list(smoothing_windows or DEFAULT_SMOOTHING_WINDOWS),
    )
    _validate(config)
    return config


def _config_from_dict(raw: Dict[str, Any]) -> Config:
    market = _parse_market(raw.get("market"))
    windows = _parse_smoothing_windows(raw.get("smoothing_windows"))

    if "own_brand" not in raw:
        raise ConfigError("Missing required field 'own_brand'. Exactly one brand must be marked as your own.")

    brands = [_parse_brand(raw["own_brand"], "own_brand", is_own_brand=True)]

    competitors = raw.get("competitors") or []
    if not isinstance(competitors, list):
        raise ConfigError("Field 'competitors' must be a list of brands.")
    if not competitors:
        raise ConfigError(
            "Field 'competitors' is empty. Share of Search is a ratio against a "
            "category — you need at least one competitor for the number to mean anything."
        )
    for index, entry in enumerate(competitors):
        brands.append(_parse_brand(entry, f"competitors[{index}]", is_own_brand=False))

    config = Config(market=market, brands=brands, smoothing_windows=windows)
    _validate(config)
    return config


def _parse_market(raw: Any) -> Market:
    if raw is None:
        raise ConfigError("Missing required field 'market'. It needs 'name', 'location_code' and 'language_code'.")
    if not isinstance(raw, dict):
        raise ConfigError("Field 'market' must be a mapping with 'name', 'location_code' and 'language_code'.")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("Field 'market.name' must be a non-empty string, e.g. 'US'.")

    location_code = raw.get("location_code")
    if isinstance(location_code, bool) or not isinstance(location_code, int):
        raise ConfigError(
            "Field 'market.location_code' must be an integer DataForSEO location code "
            "(2840 = United States, 2826 = United Kingdom)."
        )
    if location_code <= 0:
        raise ConfigError(f"Field 'market.location_code' must be positive, got {location_code}.")

    language_code = raw.get("language_code", "en")
    if not isinstance(language_code, str) or not (2 <= len(language_code) <= 5):
        raise ConfigError(
            f"Field 'market.language_code' must be a 2-5 character language code like 'en', got {language_code!r}."
        )

    return Market(name=name.strip(), location_code=location_code, language_code=language_code.strip())


def _parse_smoothing_windows(raw: Any) -> List[int]:
    if raw is None:
        return list(DEFAULT_SMOOTHING_WINDOWS)
    if not isinstance(raw, list) or not raw:
        raise ConfigError("Field 'smoothing_windows' must be a non-empty list of month counts, e.g. [3, 12].")
    windows = []
    for window in raw:
        if isinstance(window, bool) or not isinstance(window, int) or window < 2:
            raise ConfigError(
                f"Field 'smoothing_windows' must contain integers of 2 or more months, got {window!r}."
            )
        windows.append(window)
    return sorted(set(windows))


def _parse_brand(raw: Any, field_path: str, is_own_brand: bool) -> Brand:
    if not isinstance(raw, dict):
        raise ConfigError(f"Field '{field_path}' must be a mapping with at least 'name' and 'keywords'.")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"Field '{field_path}.name' must be a non-empty string.")

    keywords = raw.get("keywords")
    if isinstance(keywords, str):
        keywords = [keywords]
    if not isinstance(keywords, list) or not keywords:
        raise ConfigError(
            f"Field '{field_path}.keywords' must be a non-empty list of search terms "
            f"(brand '{name}' has none)."
        )

    cleaned = []
    for keyword in keywords:
        if not isinstance(keyword, str) or not keyword.strip():
            raise ConfigError(f"Field '{field_path}.keywords' contains an empty or non-string entry.")
        cleaned.append(keyword.strip().lower())

    url = raw.get("url")
    if url is not None and not isinstance(url, str):
        raise ConfigError(f"Field '{field_path}.url' must be a string if present.")

    ambiguous = raw.get("ambiguous", False)
    if not isinstance(ambiguous, bool):
        raise ConfigError(f"Field '{field_path}.ambiguous' must be true or false, got {ambiguous!r}.")

    return Brand(
        name=name.strip(),
        keywords=cleaned,
        is_own_brand=is_own_brand,
        url=url.strip() if isinstance(url, str) else None,
        ambiguous=ambiguous,
    )


def _validate(config: Config) -> None:
    """Cross-field checks that can only run once every brand is parsed."""
    own = [b for b in config.brands if b.is_own_brand]
    if len(own) != 1:
        raise ConfigError(f"Exactly one brand must be marked as own_brand, found {len(own)}.")

    seen_names = {}
    for brand in config.brands:
        key = brand.name.casefold()
        if key in seen_names:
            raise ConfigError(
                f"Duplicate brand name '{brand.name}'. Brand names must be unique — "
                "they're the key the whole data store is built on."
            )
        seen_names[key] = brand.name

    # A keyword shared between two brands would be double-counted in the
    # category total and inflate both brands' share.
    keyword_owner: Dict[str, str] = {}
    for brand in config.brands:
        for keyword in brand.keywords:
            if keyword in keyword_owner and keyword_owner[keyword] != brand.name:
                raise ConfigError(
                    f"Keyword '{keyword}' is assigned to both '{keyword_owner[keyword]}' and "
                    f"'{brand.name}'. A keyword can only belong to one brand, or its volume "
                    "gets counted twice in the category total."
                )
            keyword_owner[keyword] = brand.name

    if len(config.brands) < 2:
        raise ConfigError("A category needs at least two brands for share to mean anything.")


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def load_dotenv(path: Optional[Path] = None) -> bool:
    """Load ``KEY=value`` pairs from a .env file into the environment.

    A deliberately small parser so the tool has no dependency on python-dotenv.
    Existing environment variables always win, and nothing read here is ever
    echoed back to the terminal. Returns True if a file was found.
    """
    path = Path(path) if path else Path(".env")
    if not path.exists():
        return False

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def credential_env_var() -> Optional[str]:
    """Return the name of the env var supplying the login, or None.

    Returns the *name* only. Credential values are never returned by this
    function, logged, or printed anywhere in this tool.
    """
    for name in LOGIN_ENV_VARS:
        if os.environ.get(name):
            return name
    return None


def get_credentials() -> "tuple[str, str]":
    """Read DataForSEO credentials from the environment.

    Raises ConfigError naming the missing variables — never their values.
    """
    login_var = credential_env_var()
    login = os.environ.get(login_var) if login_var else None
    password = os.environ.get(PASSWORD_ENV_VAR)

    missing = []
    if not login:
        missing.append(LOGIN_ENV_VARS[0])
    if not password:
        missing.append(PASSWORD_ENV_VAR)

    if missing:
        raise ConfigError(
            f"Missing environment variable(s): {', '.join(missing)}.\n"
            "  Copy .env.example to .env and fill it in, or export them in your shell:\n"
            "    export DATAFORSEO_LOGIN='your-login'\n"
            "    export DATAFORSEO_PASSWORD='your-password'\n"
            "  Sign up at https://dataforseo.com/ — a run costs about $0.075."
        )

    return login, password

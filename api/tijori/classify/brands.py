"""Shared brand dictionary (PLAN §7.4 step 3): brand patterns → canonical name → category.

Seeded from the 2026-09-26 regex prototype plus common Indian brands. Brands only: people,
local shops and personal UPI handles never belong here; those are learned per member.
Order matters: the first matching entry wins, so specific entries precede broad ones.
Patterns run against upper-cased text built by `merchants.brand_haystack`.
"""

from dataclasses import dataclass

VERSION = "2026-09-26.1"


@dataclass(frozen=True, slots=True)
class Brand:
    key: str
    name: str
    category: str
    patterns: tuple[str, ...]


def _b(key: str, name: str, category: str, *patterns: str) -> Brand:
    return Brand(key, name, category, patterns)


BRANDS: tuple[Brand, ...] = (
    # Card bills and investments are consumed by the structural kind rules.
    _b("cred", "CRED", "Card bill payment", r"\bCRED\b", r"CRED\.CLUB", r"CREDCLUB"),
    _b("groww", "Groww", "Investments", r"GROWW", r"GR0WW", r"NEXTBILLION"),
    _b("zerodha", "Zerodha", "Investments", r"ZERODHA"),
    _b("upstox", "Upstox", "Investments", r"UPSTOX", r"\bRKSV\b"),
    _b("angel_one", "Angel One", "Investments", r"ANGEL ?ONE", r"ANGEL ?BROKING"),
    _b("kuvera", "Kuvera", "Investments", r"KUVERA"),
    _b("paytm_money", "Paytm Money", "Investments", r"PAYTM ?MONEY"),
    _b("iccl", "ICCL (mutual funds)", "Investments", r"\bICCL\b", r"INDIAN CLEARING"),
    _b("mf_utilities", "MF Utilities", "Investments", r"MF ?UTILITIES"),
    _b("cams", "CAMS", "Investments", r"\bCAMS\b", r"CAMSONLINE"),
    _b("kfintech", "KFintech", "Investments", r"KFIN", r"KARVY"),
    _b("nps", "NPS", "Investments", r"\bNPS TRUST\b", r"\bNPS\b"),
    # Groceries
    _b("instamart", "Swiggy Instamart", "Groceries", r"INSTAMART"),
    _b("amazon_fresh", "Amazon Fresh", "Groceries", r"AMAZON ?FRESH"),
    _b("blinkit", "Blinkit", "Groceries", r"BLINKIT", r"GROFERS", r"\bBLINK CO", r"BLINKCOMME"),
    _b("jiomart", "JioMart", "Groceries", r"JIOMART"),
    _b("zepto", "Zepto", "Groceries", r"ZEPTO", r"KIRANAKART"),
    _b("bigbasket", "BigBasket", "Groceries", r"BIGBASKET", r"\bBBNOW\b", r"INNOVATIVE RETAIL"),
    _b("dmart", "DMart", "Groceries", r"\bDMART\b", r"AVENUE SUPERMARTS"),
    # Eating out
    _b("zomato", "Zomato", "Eating out", r"ZOMATO", r"ETERNALTSP", r"\bETERNAL (?:LTD|LIMITED)\b"),
    _b("swiggy", "Swiggy", "Eating out", r"SWIGGY", r"\bBUNDL\b"),
    _b("dominos", "Domino's", "Eating out", r"DOMINO", r"JUBILANT ?FOOD"),
    _b("burger_king", "Burger King", "Eating out", r"BURGER ?KING", r"\bBURGER K\b", r"RESTAURANT BRANDS ASIA"),
    _b("mcdonalds", "McDonald's", "Eating out", r"MCDONALD", r"HARDCASTLE REST"),
    _b("kfc", "KFC", "Eating out", r"\bKFC\b", r"DEVYANI"),
    _b("pizza_hut", "Pizza Hut", "Eating out", r"PIZZA ?HUT"),
    _b("starbucks", "Starbucks", "Eating out", r"STARBUCKS"),
    _b("chilis", "Chili's", "Eating out", r"\bCHILI'?S\b"),
    _b("haldirams", "Haldiram's", "Eating out", r"HALDIRAM"),
    _b("barbeque_nation", "Barbeque Nation", "Eating out", r"BARBEQUE ?NATION"),
    _b("eatsure", "EatSure", "Eating out", r"EATSURE", r"REBEL FOODS"),
    # Travel (tolls first: the FASTag handle is a gpay-* handle)
    _b("fastag", "FASTag toll", "Travel", r"GPAY-TOLL", r"FASTAG", r"\bNETC\b", r"IHMCL"),
    _b("fuel", "Fuel", "Travel", r"INDIAN ?OIL", r"\bIOCL\b", r"\bBPCL\b", r"BHARAT PETROLEUM",
       r"\bHPCL\b", r"HINDUSTAN PETROLEUM", r"NAYARA", r"JIO[- ]BP"),
    _b("booking_com", "Booking.com", "Travel", r"BOOKING\.CO", r"BOOKING COM"),
    _b("makemytrip", "MakeMyTrip", "Travel", r"MAKEMYTRIP", r"\bMMT\b"),
    _b("goibibo", "Goibibo", "Travel", r"GOIBIBO", r"\bIBIBO\b"),
    _b("cleartrip", "Cleartrip", "Travel", r"CLEARTRIP"),
    _b("easemytrip", "EaseMyTrip", "Travel", r"EASEMYTRIP", r"EASE MY TRIP"),
    _b("ixigo", "ixigo", "Travel", r"IXIGO", r"LE TRAVENUES"),
    _b("irctc", "IRCTC", "Travel", r"IRCTC"),
    _b("redbus", "redBus", "Travel", r"REDBUS"),
    _b("uber", "Uber", "Travel", r"\bUBER\b"),
    _b("ola", "Ola", "Travel", r"\bOLA ?CABS\b", r"ANI TECHNOLOGIES", r"\bOLACABS\b"),
    _b("rapido", "Rapido", "Travel", r"RAPIDO", r"ROPPEN"),
    _b("indigo", "IndiGo", "Travel", r"\bINDIGO\b", r"INTERGLOBE"),
    _b("air_india", "Air India", "Travel", r"AIR ?INDIA"),
    _b("akasa", "Akasa Air", "Travel", r"AKASA"),
    _b("airbnb", "Airbnb", "Travel", r"AIRBNB"),
    _b("oyo", "OYO", "Travel", r"\bOYO\b", r"ORAVEL"),
    # Bills & subscriptions (Amazon's services before Amazon shopping)
    _b("amazon_prime", "Amazon Prime", "Bills & subscriptions", r"AMAZON.*\bPRIME\b", r"PRIME ?VIDEO"),
    _b("aws", "AWS", "Bills & subscriptions", r"AMAZONAWS", r"AWS INDIA", r"AMAZON WEB SERVICES"),
    _b("apple_store", "Apple India", "Shopping", r"APPLE ?INDIA", r"\bAPPLE IN\b", r"APPLE STORE"),
    _b("apple_services", "Apple services", "Bills & subscriptions", r"APPLE ?SERVI", r"\bAPPLE ?ME\b",
       r"\bAPPLE ?SE\b", r"APPLE MEDIA", r"ITUNES", r"APPLE\.COM/BILL"),
    _b("google_play", "Google Play", "Bills & subscriptions", r"PLAYSTORE", r"GOOGLE ?PLAY", r"PLAY ?STORE"),
    _b("youtube", "YouTube", "Bills & subscriptions", r"YOUTUBE"),
    _b("google_one", "Google One", "Bills & subscriptions", r"GOOGLE ?ONE", r"GOOGLE ?STORAGE"),
    _b("gpay_recharge", "Google Pay recharge", "Bills & subscriptions", r"GPAYRECHAR", r"EURONET"),
    _b("gpay_billpay", "Google Pay bill payment", "Bills & subscriptions", r"GPAY\.BP", r"GPAYBP"),
    _b("netflix", "Netflix", "Bills & subscriptions", r"NETFLIX"),
    _b("spotify", "Spotify", "Bills & subscriptions", r"SPOTIFY"),
    _b("hotstar", "JioHotstar", "Bills & subscriptions", r"HOTSTAR", r"NOVI DIGITAL"),
    _b("zee5", "ZEE5", "Bills & subscriptions", r"ZEE5"),
    _b("sonyliv", "SonyLIV", "Bills & subscriptions", r"SONY ?LIV"),
    _b("airtel", "Airtel", "Bills & subscriptions", r"\bAIRTEL\b"),
    _b("jio", "Jio", "Bills & subscriptions", r"\bJIO\b", r"RELIANCE JIO", r"JIOFIBER"),
    _b("vi", "Vi", "Bills & subscriptions", r"VODAFONE", r"VODAIDEA", r"\bVI (?:PREPAID|POSTPAID)\b"),
    _b("bsnl", "BSNL", "Bills & subscriptions", r"\bBSNL\b"),
    _b("tata_play", "Tata Play", "Bills & subscriptions", r"TATA ?PLAY", r"TATA ?SKY"),
    _b("electricity", "Electricity", "Bills & subscriptions", r"ELECTRICITY", r"\bBESCOM\b", r"\bBSES\b",
       r"\bUPPCL\b", r"\bPVVNL\b", r"\bMSEDCL\b", r"\bTNEB\b"),
    _b("hostinger", "Hostinger", "Bills & subscriptions", r"HOSTINGER"),
    _b("openrouter", "OpenRouter", "Bills & subscriptions", r"OPENROUTER"),
    _b("anthropic", "Anthropic", "Bills & subscriptions", r"ANTHROPIC", r"CLAUDE\.AI"),
    _b("openai", "OpenAI", "Bills & subscriptions", r"OPENAI", r"CHATGPT"),
    _b("github", "GitHub", "Bills & subscriptions", r"GITHUB"),
    _b("google_cloud", "Google Cloud", "Bills & subscriptions", r"GOOGLE ?CLOUD", r"GOOGLE ?WORKSPACE", r"GSUITE"),
    _b("microsoft", "Microsoft", "Bills & subscriptions", r"MICROSOFT"),
    _b("adobe", "Adobe", "Bills & subscriptions", r"ADOBE"),
    _b("cloudflare", "Cloudflare", "Bills & subscriptions", r"CLOUDFLARE"),
    _b("digitalocean", "DigitalOcean", "Bills & subscriptions", r"DIGITALOCEAN"),
    _b("godaddy", "GoDaddy", "Bills & subscriptions", r"GODADDY"),
    _b("namecheap", "Namecheap", "Bills & subscriptions", r"NAMECHEAP"),
    _b("paper_design", "Paper", "Bills & subscriptions", r"PAPER\.DESIGN"),
    _b("canva", "Canva", "Bills & subscriptions", r"\bCANVA\b"),
    _b("expressvpn", "ExpressVPN", "Bills & subscriptions", r"EXPRESS ?VPN"),
    # Card statement fee and tax lines (before shopping: "IGST" lines carry merchant refs)
    _b("card_fees", "Card fees & GST", "Bank charges", r"^\s*[ICS]GST\b", r"MARKUP FEE", r"\bDCC\b", r"FEE ON GAMING",
       r"FINANCE CHARGE", r"LATE PAYMENT FEE", r"ANNUAL FEE", r"RENEWAL FEE", r"FUEL SURCHARGE"),
    _b("cashback", "Cashback", "Refunds", r"CASHBACK"),
    # Shopping
    _b("smartbuy", "HDFC SmartBuy", "Shopping", r"SMARTBUY", r"GYFTR"),
    _b("amazon", "Amazon", "Shopping", r"AMAZON", r"\bAMZN", r"AMZNLPA"),
    _b("flipkart", "Flipkart", "Shopping", r"FLIPKART"),
    _b("myntra", "Myntra", "Shopping", r"MYNTRA"),
    _b("ajio", "AJIO", "Shopping", r"\bAJIO\b"),
    _b("nykaa", "Nykaa", "Shopping", r"NYKAA", r"FSN E-?COMMERCE"),
    _b("meesho", "Meesho", "Shopping", r"MEESHO"),
    _b("tata_cliq", "Tata CLiQ", "Shopping", r"TATA ?CLIQ"),
    _b("souled_store", "The Souled Store", "Shopping", r"SOULED ?STORE", r"THESOULE", r"\bTHE SOUL\b"),
    _b("decathlon", "Decathlon", "Shopping", r"DECATHLON"),
    _b("ikea", "IKEA", "Shopping", r"\bIKEA\b"),
    _b("croma", "Croma", "Shopping", r"CROMA", r"INFINITI RETAIL"),
    _b("reliance_digital", "Reliance Digital", "Shopping", r"RELIANCE DIGITAL"),
    _b("lenskart", "Lenskart", "Shopping", r"LENSKART"),
    _b("uniqlo", "Uniqlo", "Shopping", r"UNIQLO"),
    _b("zara", "Zara", "Shopping", r"\bZARA\b"),
    _b("hm", "H&M", "Shopping", r"\bH ?& ?M\b", r"HENNES"),
    # Health
    _b("apollo", "Apollo", "Health", r"\bAPOLLO\b"),
    _b("pharmeasy", "PharmEasy", "Health", r"PHARMEASY"),
    _b("tata_1mg", "Tata 1mg", "Health", r"\b1MG\b"),
    _b("netmeds", "Netmeds", "Health", r"NETMEDS"),
    _b("medplus", "MedPlus", "Health", r"MEDPLUS"),
    _b("practo", "Practo", "Health", r"PRACTO"),
    _b("cult_fit", "cult.fit", "Health", r"CULT\.?FIT", r"CUREFIT"),
    # Insurance
    _b("plum", "Plum (health cover)", "Insurance", r"PLUM ?BEN", r"PLUMBENEFI"),
    _b("hdfc_life", "HDFC Life", "Insurance", r"HDFC ?LIFE"),
    _b("lic", "LIC", "Insurance", r"\bLIC\b", r"LIFE INSURANCE CORP", r"LICOFINDIA"),
    _b("icici_lombard", "ICICI Lombard", "Insurance", r"ICICI ?LOMBARD"),
    _b("icici_pru", "ICICI Prudential Life", "Insurance", r"ICICI ?PRU"),
    _b("star_health", "Star Health", "Insurance", r"STAR ?HEALTH"),
    _b("acko", "ACKO", "Insurance", r"\bACKO\b"),
    _b("digit", "Digit Insurance", "Insurance", r"GO ?DIGIT"),
    _b("policybazaar", "Policybazaar", "Insurance", r"POLICY ?BAZAAR", r"PB FINTECH"),
    _b("niva_bupa", "Niva Bupa", "Insurance", r"NIVA ?BUPA", r"MAX ?BUPA"),
    _b("tata_aig", "Tata AIG", "Insurance", r"TATA ?AIG"),
    # Tax
    _b("income_tax", "Income tax", "Tax", r"INCOMETAX", r"INCOME ?TAX", r"\bCBDT\b", r"CENTRAL BOARD OF DIR",
       r"\bOLTAS\b", r"TIN ?NSDL"),
    # Services
    _b("csc", "Digital Seva CSC", "Services", r"\bDSCSC", r"DIGITAL SEVA", r"CSC E-?GOV"),
    _b("urban_company", "Urban Company", "Services", r"URBAN ?COMPANY", r"URBANCLAP"),
    _b("passport_seva", "Passport Seva", "Services", r"PASSPORT ?SEVA"),
    # Entertainment
    _b("bookmyshow", "BookMyShow", "Entertainment", r"BOOKMYSHOW", r"BIGTREE"),
    _b("pvr_inox", "PVR INOX", "Entertainment", r"\bPVR\b", r"\bINOX\b"),
    _b("steam", "Steam", "Entertainment", r"STEAMGAMES", r"STEAM ?POWERED", r"\bVALVE\b"),
    _b("playstation", "PlayStation", "Entertainment", r"PLAYSTATION", r"SONY INTERACTIVE"),
    _b("xbox", "Xbox", "Entertainment", r"\bXBOX\b"),
)

# Brands whose recurring charges are a service you sign up for, as opposed to a utility bill or a
# premium; services/recurring groups a detected series by this. Only used once a series is steady.
SUBSCRIPTIONS: frozenset[str] = frozenset({
    "amazon_prime", "aws", "apple_services", "google_play", "youtube", "google_one", "netflix", "spotify",
    "hotstar", "zee5", "sonyliv", "tata_play", "hostinger", "openrouter", "anthropic", "openai", "github",
    "google_cloud", "microsoft", "adobe", "cloudflare", "digitalocean", "godaddy", "namecheap", "cult_fit",
    "playstation", "xbox", "steam", "paper_design", "canva", "expressvpn",
})

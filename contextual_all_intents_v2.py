# contextual_all_intents_v2.py
from math import isfinite

# ---------- CONFIG / SEED INPUTS (replace with live data) ----------
countries = ["Senegal", "DRC", "CoteIvoire", "Ethiopia", "South Africa"]
actors = ["China","France","UnitedStates","Russia","Rwanda","Saudi","Turkey","UAE","Israel","Iran","NonState"]

# GDP (USD)
GDP = {"Senegal":33.6e9, "DRC":70.75e9, "CoteIvoire":86.54e9, "Ethiopia":125.0e9, "South Africa":400.26e9}

# Debt (USD) direct + traceable (seeded from earlier pass)
DEBT = {
  "China": {"Senegal":1410666722.69, "DRC":2029900000.0, "CoteIvoire":793390000.0,"Ethiopia":4000000000.0, "South Africa":21300000000.0},
  "France":{"Senegal":280800000.0, "DRC":0.0, "CoteIvoire":523800000.0, "Ethiopia":200000000.0, "South Africa":756000000.0},
  "UnitedStates":{"Senegal":91500000.0, "DRC":0.0, "CoteIvoire":0.0,"Ethiopia":100000000.0, "South Africa":0.0},
  "Saudi":{"Senegal":110000000.0, "DRC":0.0, "CoteIvoire":0.0, "Ethiopia":50000000.0, "South Africa":0.0},
  "UAE":{"Senegal":65600000.0, "DRC":0.0, "CoteIvoire":0.0, "Ethiopia":100000000.0, "South Africa":0.0},
  # others default 0
  "Russia":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":50000000.0, "South Africa":0.0},
  "Turkey":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":200000000.0, "South Africa":0.0},
  "Israel":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":50000000.0, "South Africa":0.0},
  "Iran":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":10000000.0, "South Africa":0.0},
  "Rwanda":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":0.0, "South Africa":0.0},
  "NonState":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":0.0, "South Africa":0.0}
}

# resource proxy (0..1) - operator/offtake / conservative proxies (replace with UNComtrade/EITI-derived shares)
G_RES = {
  "China":{"Senegal":0.10,"DRC":0.60,"CoteIvoire":0.09, "Ethiopia":0.70, "South Africa":0.20},
  "France":{"Senegal":0.05,"DRC":0.05,"CoteIvoire":0.20,"Ethiopia":0.10, "South Africa":0.08},
  "UnitedStates":{"Senegal":0.40,"DRC":0.05,"CoteIvoire":0.0,"Ethiopia":0.15, "South Africa":0.10},
  "Russia":{"Senegal":0.0,"DRC":0.10,"CoteIvoire":0.0,"Ethiopia":0.10, "South Africa":0.03},
  "NonState":{"Senegal":0.05,"DRC":0.05,"CoteIvoire":0.05,"Ethiopia":0.05, "South Africa":0.89},
  # fill others with zeros or your data
  "Saudi":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":0.20, "South Africa":0.04},
  "UAE":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":0.50,"South Africa":0.09},
  "Turkey":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":0.60, "South Africa":0.05},
  "Israel":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":0.10, "South Africa":0.06},
  "Iran":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":0.0, "South Africa":0.03},
  "Rwanda":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0,"Ethiopia":0.0, "South Africa":0.0}
}

# military presence tiers (none=0, training=0.33, rotational=0.66, base=1.0)
G_MIL = {
  "China":{"Senegal":0.33,"DRC":0.33,"CoteIvoire":0.0,"Ethiopia":0.33, "South Africa":0.33},
  "France":{"Senegal":0.0,"DRC":0.33,"CoteIvoire":0.33,"Ethiopia":0.0, "South Africa":0.66},
  "UnitedStates":{"Senegal":0.66,"DRC":0.33,"CoteIvoire":0.66,"Ethiopia":0.66, "South Africa":0.66},
  "Russia":{"Senegal":0.0,"DRC":0.33,"CoteIvoire":0.10,"Ethiopia":0.50, "South Africa":0.33},
  "Rwanda":{"Senegal":0.0,"DRC":0.33,"CoteIvoire":0.0, "Ethiopia":0.0, "South Africa":0.00},
  "NonState":{"Senegal":0.0,"DRC":1.00,"CoteIvoire":0.0, "Ethiopia":0.33, "South Africa":0.33},
  "Saudi":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":0.33, "South Africa":0.00},
  "UAE":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":0.33, "South Africa":0.33},
  "Turkey":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":0.33, "South Africa":0.00},
  "Israel":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":0.33, "South Africa":0.33},
  "Iran":{"Senegal":0.0,"DRC":0.0,"CoteIvoire":0.0, "Ethiopia":0.0, "South Africa":0.00}
}

# FSI raw & normalization (global min/max used)
FSI_RAW = {"Senegal":74.2, "DRC":106.7, "CoteIvoire":85.3, "Ethiopia":98.1, "South Africa":69.6}
FSI_MIN, FSI_MAX = 22.0, 120.0
FSI_NORM = {c: max(0.0, min(1.0, (FSI_RAW[c]-FSI_MIN)/(FSI_MAX-FSI_MIN))) for c in countries}
# Example results: Senegal ~0.618, DRC ~0.889, CI ~0.711

# ILGA L_c enforcement for LGBT (1=high enforcement)
L = {"Senegal":0.90, "DRC":0.20, "CoteIvoire":0.20,"Ethiopia":0.95, "South Africa":0.05}

# ---------- Actor × Country dimension scores used to make composite actor indices ----------
# These were seeded from the dimension-scoring I used in the message above.
# For production, compute these from automated signals / databases (media counts, grants, press flags, etc.)

# actor_disinfo_index dimension seeds (averages already computed)
ACTOR_DISINFO = {
  "China":{"Senegal":0.46,"DRC":0.50,"CoteIvoire":0.40,"Ethiopia": 0.35, "South Africa":0.64},
  "France":{"Senegal":0.84,"DRC":0.44,"CoteIvoire":0.82, "Ethiopia":0.60, "South Africa":0.32},
  "UnitedStates":{"Senegal":0.58,"DRC":0.24,"CoteIvoire":0.34,"Ethiopia":0.50, "South Africa":0.62},
  "Russia":{"Senegal":0.24,"DRC":0.50,"CoteIvoire":0.20,"Ethiopia":0.65, "South Africa":0.72},
  "Rwanda":{"Senegal":0.12,"DRC":0.56,"CoteIvoire":0.12, "Ethiopia":0.05, "South Africa":0.22},
  "Saudi":{"Senegal":0.25,"DRC":0.01,"CoteIvoire":0.02,"Ethiopia":0.10, "South Africa":0.46},
  "UAE":{"Senegal":0.25,"DRC":0.02,"CoteIvoire":0.02, "Ethiopia":0.60, "South Africa":0.64},
  "Turkey":{"Senegal":0.20,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.40, "South Africa":0.46},
  "Israel":{"Senegal":0.10,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.20, "South Africa":0.72},
  "Iran":{"Senegal":0.08,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.02, "South Africa":0.42},
  "NonState":{"Senegal":0.42,"DRC":0.64,"CoteIvoire":0.44,"Ethiopia":0.55, "South Africa":0.64}
}

# actor_elec_index seeds (averages from election-dimensions)
ACTOR_ELEC = {
  "China":{"Senegal":0.32,"DRC":0.50,"CoteIvoire":0.10, "Ethiopia":0.40,"South Africa":0.54},
  "France":{"Senegal":0.68,"DRC":0.08,"CoteIvoire":0.80,"Ethiopia":0.60,"South Africa":0.34},
  "UnitedStates":{"Senegal":0.46,"DRC":0.06,"CoteIvoire":0.06,"Ethiopia":0.75,"South Africa":0.60},
  "Russia":{"Senegal":0.10,"DRC":0.25,"CoteIvoire":0.01,"Ethiopia":0.40,"South Africa":0.54},
  "Rwanda":{"Senegal":0.10,"DRC":0.70,"CoteIvoire":0.05,"Ethiopia":0.0,"South Africa":0.14},
  "NonState":{"Senegal":0.02,"DRC":0.10,"CoteIvoire":0.05,"Ethiopia":0.30,"South Africa":0.52},
  # others small values...
  "Saudi":{"Senegal":0.05,"DRC":0.01,"CoteIvoire":0.01,"Ethiopia":0.20,"South Africa":0.44},
  "UAE":{"Senegal":0.05,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.50,"South Africa":0.56},
  "Turkey":{"Senegal":0.02,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.20,"South Africa":0.38},
  "Israel":{"Senegal":0.03,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.30,"South Africa":0.56},
  "Iran":{"Senegal":0.02,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.02,"South Africa":0.36}
}

# actor_lgbtq_index seeds
ACTOR_LGBTQ = {
  "UnitedStates":{"Senegal":0.70,"DRC":0.14,"CoteIvoire":0.14, "Ethiopia":0.80,"South Africa":0.85},
  "France":{"Senegal":0.65,"DRC":0.13,"CoteIvoire":0.65,"Ethiopia":0.70,"South Africa":0.55},
  "China":{"Senegal":0.05,"DRC":0.05,"CoteIvoire":0.05,"Ethiopia":0.05,"South Africa":0.03},
  "Russia":{"Senegal":0.02,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.02,"South Africa":0.00},
  "NonState":{"Senegal":0.00,"DRC":0.00,"CoteIvoire":0.00,"Ethiopia":0.00,"South Africa":0.50},
  # others small values
  "Saudi":{"Senegal":0.01,"DRC":0.01,"CoteIvoire":0.01,"Ethiopia":0.01,"South Africa":0.00},
  "UAE":{"Senegal":0.02,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.02,"South Africa":0.00},
  "Turkey":{"Senegal":0.02,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.02,"South Africa":0.08},
  "Israel":{"Senegal":0.01,"DRC":0.01,"CoteIvoire":0.01,"Ethiopia":0.01,"South Africa":0.38},
  "Iran":{"Senegal":0.01,"DRC":0.01,"CoteIvoire":0.01,"Ethiopia":0.01,"South Africa":0.00},
  "Rwanda":{"Senegal":0.02,"DRC":0.02,"CoteIvoire":0.02,"Ethiopia":0.02,"South Africa":0.08}
}

# ---------- INTENT definitions (frag excluded outside social fragility) ----------
INTENT_FACTORS = {
 "Economic": ["debt","res"],
 "Sovereignty": ["debt","mil","elec"],
 "LGBTQ": ["lgbt","elec"],
 "Religious": ["elec","mil"],   # religion uses elec + mil as primary drivers for polarization
 "ElectionInfluence": ["elec","debt","mil"],
 "MilitaryPresence": ["mil","debt"],
 "ResourceDependency": ["res","debt"],
 "SocialFragility": ["frag","debt","mil"]   # only place where frag is used
}

# ---------- HELPERS ----------
def clip(x): return max(0.0, min(1.0, float(x)))
def presence_factor(a,c):
    # presence=1 if debt>0 or g_mil>=0.33 or g_res>=0.10 or actor_disinfo>0.3
    if DEBT.get(a,{}).get(c,0.0) > 0: return 1.0
    if G_MIL.get(a,{}).get(c,0.0) >= 0.33: return 1.0
    if G_RES.get(a,{}).get(c,0.0) >= 0.10: return 1.0
    # else if some small sign
    if any([G_MIL.get(a,{}).get(c,0.0) > 0, G_RES.get(a,{}).get(c,0.0) > 0, ACTOR_DISINFO.get(a,{}).get(c,0.0) > 0.15]):
        return 0.5
    return 0.2


from datetime import datetime

ELECTION_DATES = {
    "Senegal":      datetime(2027, 2, 25),    # example – next presidential election
    "DRC":          datetime(2028, 12, 20),   # example – general election
    "CoteIvoire":   datetime(2025, 10, 31),   # presidential election
    "Ethiopia":     datetime(2026, 6, 30),    # general election (as discussed)
    "South Africa": datetime(2029, 5, 15),    # national election
}

def months_until_election(country, current_date=None):
    """Return number of months until the next election for the given country.
    Returns 999 if no election date is defined or if the election has already passed."""
    if current_date is None:
        current_date = datetime.now()
    election_date = ELECTION_DATES.get(country)
    if not election_date:
        return 999
    if election_date <= current_date:
        return 0          # election already happened
    # Calculate months difference
    months = (election_date.year - current_date.year) * 12 + (election_date.month - current_date.month)
    # Adjust for day of month (if election day is earlier in the month, subtract 1)
    if election_date.day < current_date.day:
        months -= 1
    return max(0, months)

# compute g_f values
from datetime import datetime

ELECTION_DATES = {
    "Senegal":      datetime(2027, 2, 25),    # example – next presidential election
    "DRC":          datetime(2028, 12, 20),   # example – general election
    "CoteIvoire":   datetime(2025, 10, 31),   # presidential election
    "Ethiopia":     datetime(2026, 6, 30),    # general election (as discussed)
    "South Africa": datetime(2029, 5, 15),    # national election
}

def months_until_election(country, current_date=None):
    """Return number of months until the next election for the given country.
    Returns 999 if no election date is defined or if the election has already passed."""
    if current_date is None:
        current_date = datetime.now()
    election_date = ELECTION_DATES.get(country)
    if not election_date:
        return 999
    if election_date <= current_date:
        return 0          # election already happened
    # Calculate months difference
    months = (election_date.year - current_date.year) * 12 + (election_date.month - current_date.month)
    # Adjust for day of month (if election day is earlier in the month, subtract 1)
    if election_date.day < current_date.day:
        months -= 1
    return max(0, months)


def compute_gs():
    """Compute g (actor‑country specific factors) for all actors and countries."""
    g = {a: {c: {} for c in COUNTRIES} for a in ACTORS}
    for a in ACTORS:
        for c in COUNTRIES:
            # ----- debt factor -----
            debt = DEBT.get(a, {}).get(c, 0.0)
            g_debt = clip(debt / GDP[c]) if GDP[c] > 0 else 0.0

            # ----- resource factor -----
            g_res = G_RES.get(a, {}).get(c, 0.0)

            # ----- military factor -----
            g_mil = G_MIL.get(a, {}).get(c, 0.0)

            # ----- election factor (dynamic) -----
            months_to_elec = months_until_election(c)
            # g_elec_time = 1 - min(months_to_elec, 24) / 24  (0 if >24 months)
            g_elec_time = 1 - min(months_to_elec, 24) / 24 if months_to_elec < 999 else 0.0
            elec_index = ACTOR_ELEC.get(a, {}).get(c, 0.0)
            # baseline presence factor (used to avoid zero when months_to_elec is large)
            base = 0.25 * presence_factor(a, c)
            g_elec = elec_index * max(g_elec_time, base)

            # ----- lgbt factor -----
            lgbt_index = ACTOR_LGBTQ.get(a, {}).get(c, 0.0)
            g_lgbt = (1 - L.get(c, 0.5)) * lgbt_index

            # ----- fragility / disinfo factor -----
            disinfo_index = ACTOR_DISINFO.get(a, {}).get(c, 0.0)
            g_frag = FSI_NORM[c] * disinfo_index
            hanna-tes-patch-6


            # Store all factors
            g[a][c] = {
                "debt": g_debt,
                "res":  g_res,
                "mil":  g_mil,
                "elec": g_elec,
                "lgbt": g_lgbt,
                "frag": g_frag
            }
    return g
main

            # Store all factors
            g[a][c] = {
                "debt": g_debt,
                "res":  g_res,
                "mil":  g_mil,
                "elec": g_elec,
                "lgbt": g_lgbt,
                "frag": g_frag
            }
    return g
  
# raw metrics m_{a,c,f} used for R-factors
def raw_metrics(a,c,g):
    return {
        "debt": DEBT.get(a,{}).get(c,0.0),
        "mil": G_MIL.get(a,{}).get(c,0.0),
        "res": G_RES.get(a,{}).get(c,0.0),
        "elec": g[a][c]["elec"],
        "lgbt": g[a][c]["lgbt"],
        "frag": g[a][c]["frag"]
    }

def compute_R(g):
    R = {a:{c:{} for c in countries} for a in actors}
    for a in actors:
        # compute max per factor across countries
        max_per = {}
        for f in ["debt","mil","res","elec","lgbt","frag"]:
            vals = [raw_metrics(a,c,g)[f] for c in countries]
            max_per[f] = max(vals) if vals else 0.0
        for c in countries:
            m = raw_metrics(a,c,g)
            for f in ["debt","mil","res","elec","lgbt","frag"]:
                R[a][c][f] = (m[f] / max_per[f]) if max_per[f] > 0 else 0.0
    return R

def compute_CAs(g,R):
    CA = {intent:{a:{c:0.0 for c in countries} for a in actors} for intent in INTENT_FACTORS}
    for intent,factors in INTENT_FACTORS.items():
        for a in actors:
            for c in countries:
                denom = sum(R[a][c].get(f,0.0) for f in factors)
                if denom == 0:
                    w = {f:1.0/len(factors) for f in factors}
                else:
                    w = {f:(R[a][c].get(f,0.0)/denom) for f in factors}
                CA_val = sum(w[f]*g[a][c].get(f,0.0) for f in factors)
                CA[intent][a][c] = clip(CA_val)
    return CA

def compute_finalrisk(CA, avg_base_map):
    final = {intent:{a:{c:None for c in countries} for a in actors} for intent in INTENT_FACTORS}
    for intent in CA:
        for a in actors:
            for c in countries:
                avg = None
                if isinstance(avg_base_map, dict):
                    avg = avg_base_map.get(a,{}).get(c, None) or avg_base_map.get((a,c), None)
                if avg is None:
                    raise ValueError("Please supply avg_base per (actor,country) either as nested dict or (actor,country) keys")
                avg = clip(avg)
                ca = CA[intent][a][c]
                final[intent][a][c] = clip(avg + (1.0 - avg) * ca)
    return final

# ------------------ RUN (example) ------------------
g = compute_gs()
R = compute_R(g)
CA = compute_CAs(g,R)

# example avg_base map placeholder (replace with your per-(actor,country) averages)
avg_base_example = {a:{c:0.40 for c in countries} for a in actors}
final_example = compute_finalrisk(CA, avg_base_example)

# 'CA' contains the intent CAs (actor->country->intent)
# 'final_example' contains FinalRisk per intent given avg_base_example

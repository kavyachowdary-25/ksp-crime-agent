#!/usr/bin/env python3
"""
KSP synthetic FIR data generator — matches the official ER diagram exactly.

Outputs one CSV per table (loadable into Postgres, or Catalyst Data Store
via bulk import API).

PLANTED PATTERNS (so the demo has stories to find):
  P1  Repeat offender under transliterated name variants
      ("Manjunath" / "Manjunatha" / "Manjunath Gowda", consistent age drift)
      -> proves entity resolution works
  P2  Organized chain-snatching gang: 6 people who co-occur as accused
      across ~15 FIRs in Bengaluru City -> community detection finds them
  P3  Geographic hotspot: vehicle theft cluster around Majestic/KR Market
      coordinates -> hotspot analysis lights up
  P4  Seasonal pattern: house burglaries spike Oct-Dec (festival season)
      -> trend analysis shows seasonality
  P5  An IO with an unusually high 'B' (false case) chargesheet rate
      -> analyst-level insight

Usage:  python3 generate_data.py --firs 50000 --out ./data
"""

import argparse
import csv
import os
import random
from datetime import date, datetime, timedelta

random.seed(none)

# ------------------------------------------------------------------
# Reference data — Karnataka flavored
# ------------------------------------------------------------------

DISTRICTS = [
    (1, "Bengaluru City"), (2, "Bengaluru Rural"), (3, "Mysuru"),
    (4, "Mangaluru City"), (5, "Belagavi"), (6, "Kalaburagi"),
    (7, "Hubballi-Dharwad"), (8, "Shivamogga"),
]

# (unit_id, name, district_id, lat, lon) — coordinates approximate
STATIONS = [
    (6, "Upparpet PS", 1, 12.9767, 77.5713),
    (7, "KR Market PS", 1, 12.9591, 77.5766),
    (8, "Cubbon Park PS", 1, 12.9763, 77.5929),
    (9, "Jayanagar PS", 1, 12.9308, 77.5838),
    (10, "Koramangala PS", 1, 12.9352, 77.6245),
    (11, "Whitefield PS", 1, 12.9698, 77.7500),
    (12, "Yelahanka PS", 1, 13.1007, 77.5963),
    (13, "Nelamangala PS", 2, 13.0997, 77.3906),
    (14, "Devanahalli PS", 2, 13.2437, 77.7172),
    (15, "Mysuru Lakshmipuram PS", 3, 12.3052, 76.6552),
    (16, "Mysuru VV Puram PS", 3, 12.3110, 76.6480),
    (17, "Mangaluru North PS", 4, 12.8703, 74.8420),
    (18, "Belagavi Market PS", 5, 15.8497, 74.4977),
    (19, "Kalaburagi Station Bazaar PS", 6, 17.3297, 76.8343),
    (20, "Hubballi Town PS", 7, 15.3647, 75.1240),
    (21, "Shivamogga Doddapete PS", 8, 13.9299, 75.5681),
]

CRIME_HEADS = [
    (1, "Crimes Against Body"),
    (2, "Crimes Against Property"),
    (3, "Crimes Against Women"),
    (4, "Economic Offences"),
    (5, "Cyber Crimes"),
    (6, "Crimes Against Public Order"),
]

# (subhead_id, head_id, name, weight, gravity 1=heinous 2=non-heinous)
CRIME_SUBHEADS = [
    (101, 1, "Murder", 2, 1),
    (102, 1, "Attempt to Murder", 3, 1),
    (103, 1, "Grievous Hurt", 8, 2),
    (104, 1, "Simple Hurt", 15, 2),
    (201, 2, "House Burglary", 12, 2),
    (202, 2, "Vehicle Theft", 18, 2),
    (203, 2, "Chain Snatching", 10, 2),
    (204, 2, "Robbery", 5, 1),
    (205, 2, "Ordinary Theft", 20, 2),
    (301, 3, "Cruelty by Husband/Relatives", 6, 2),
    (302, 3, "Molestation", 4, 1),
    (401, 4, "Cheating", 12, 2),
    (402, 4, "Criminal Breach of Trust", 4, 2),
    (501, 5, "Online Financial Fraud", 10, 2),
    (502, 5, "Identity Theft", 3, 2),
    (601, 6, "Rioting", 3, 2),
    (602, 6, "Unlawful Assembly", 2, 2),
]

# Act-section mapping per subhead (BNS 2023 primary, simplified)
SUBHEAD_SECTIONS = {
    101: [("BNS", "103")], 102: [("BNS", "109")],
    103: [("BNS", "117")], 104: [("BNS", "115")],
    201: [("BNS", "305"), ("BNS", "331")],
    202: [("BNS", "303")], 203: [("BNS", "304")],
    204: [("BNS", "309")], 205: [("BNS", "303")],
    301: [("BNS", "85")], 302: [("BNS", "74")],
    401: [("BNS", "318")], 402: [("BNS", "316")],
    501: [("BNS", "318"), ("IT", "66C"), ("IT", "66D")],
    502: [("IT", "66C")],
    601: [("BNS", "191")], 602: [("BNS", "189")],
}

ACTS = [
    ("BNS", "Bharatiya Nyaya Sanhita, 2023", "BNS"),
    ("IPC", "Indian Penal Code, 1860", "IPC"),
    ("IT", "Information Technology Act, 2000", "IT Act"),
    ("NDPS", "Narcotic Drugs and Psychotropic Substances Act, 1985", "NDPS"),
    ("ARMS", "Arms Act, 1959", "Arms Act"),
]

MALE_NAMES = ["Ramesh", "Suresh", "Manjunath", "Prakash", "Venkatesh", "Shivakumar",
              "Nagaraj", "Basavaraj", "Mahesh", "Kiran", "Santosh", "Raghavendra",
              "Girish", "Umesh", "Lokesh", "Harish", "Chandrashekar", "Anand",
              "Puneeth", "Darshan", "Yogesh", "Srinivas", "Muniraju", "Krishnappa"]
FEMALE_NAMES = ["Lakshmi", "Savitha", "Manjula", "Geetha", "Sunitha", "Rekha",
                "Padma", "Shobha", "Kavitha", "Asha", "Bhagya", "Nagamma",
                "Rathnamma", "Vani", "Deepa", "Shwetha", "Pallavi", "Divya"]
SURNAMES = ["Gowda", "Reddy", "Shetty", "Naik", "Rao", "Hegde", "Kumar",
            "Patil", "Poojari", "Achar", "Swamy", "Murthy", "", "", ""]

OCCUPATIONS = [(1, "Farmer"), (2, "Private Employee"), (3, "Government Employee"),
               (4, "Business"), (5, "Student"), (6, "Homemaker"), (7, "Driver"),
               (8, "Daily Wage Worker"), (9, "Unemployed"), (10, "Retired")]
RELIGIONS = [(1, "Hindu"), (2, "Muslim"), (3, "Christian"), (4, "Jain"),
             (5, "Sikh"), (6, "Buddhist"), (7, "Others")]
CASTES = [(i, f"Caste Group {i}") for i in range(1, 11)]  # anonymized groups
CASE_STATUSES = [(1, "Under Investigation"), (2, "Charge Sheeted"),
                 (3, "Closed - False Case"), (4, "Closed - Undetected"),
                 (5, "Convicted"), (6, "Acquitted")]
RANKS = [(1, "DGP", 1), (2, "SP", 3), (3, "DSP", 4), (4, "Inspector", 5),
         (5, "Sub-Inspector", 6), (6, "Head Constable", 7), (7, "Constable", 8)]
DESIGNATIONS = [(1, "SHO", 1), (2, "Investigating Officer", 2),
                (3, "Station Writer", 3), (4, "Beat Constable", 4)]

MO_TEMPLATES = {
    203: ["Two persons on a {bike} approached the complainant walking near {place} and the pillion rider snatched the gold chain weighing approx {wt} grams from her neck and sped away towards {dirn}.",
          "Unknown accused on motorcycle snatched {wt} gram gold chain from complainant near {place} at around {time} hours and escaped."],
    202: ["Complainant parked his {vehicle} bearing registration KA-{rto}-{plate} near {place}. On return the vehicle was found stolen.",
          "{vehicle} KA-{rto}-{plate} stolen from parking area near {place} between {time} and {time2} hours."],
    201: ["Unknown persons broke open the door lock of the complainant's house at {place} during {daynight} and committed theft of gold ornaments and cash worth Rs. {amt}.",
          "House breaking at {place}; accused gained entry through rear window and stole valuables worth approx Rs. {amt}."],
    501: ["Complainant received a call from unknown person posing as bank official and was induced to share OTP, following which Rs. {amt} was fraudulently transferred from his account.",
          "Online fraud: complainant lost Rs. {amt} in fake {scheme} scheme advertised on social media."],
    101: ["Due to previous enmity over {motive}, the accused assaulted the deceased with {weapon} near {place} resulting in death.",],
    104: ["Altercation between complainant and accused over {motive} near {place}; accused assaulted complainant with hands and {weapon} causing simple injuries."],
}
GENERIC_MO = "Complainant reported the incident of {crime} which occurred near {place}. Investigation taken up."

PLACES = ["Majestic Bus Stand", "KR Market", "City Railway Station", "Jayanagar 4th Block",
          "Koramangala 5th Block", "Whitefield Main Road", "Yelahanka New Town",
          "MG Road", "Shivajinagar", "Banashankari Temple", "Malleshwaram 8th Cross",
          "Electronic City Phase 1", "Hebbal Flyover", "Mysuru Devaraja Market",
          "Hubballi Old Bus Stand", "Belagavi Fort Road"]

# ------------------------------------------------------------------
# Planted pattern definitions
# ------------------------------------------------------------------

# P1: repeat offender name variants (same human, ER must link these)
P1_VARIANTS = ["Manjunath", "Manjunatha", "Manjunath Gowda", "Manjunatha G"]
P1_BIRTH_YEAR = 1992

# P2: chain-snatching gang (co-occurrence cluster)
P2_GANG = [("Ravi Kumar", 1995), ("Ravi K", 1995),  # note: Ravi also has a variant
           ("Syed Imran", 1993), ("Naveen Reddy", 1997),
           ("Muniraju", 1990), ("Santosh Naik", 1996)]

# P3: hotspot centre (Majestic) for vehicle theft
P3_CENTRE = (12.9767, 77.5713)

# P5: IO with high false-case rate
P5_IO_ID = 1007


def pick_name(gender):
    base = random.choice(MALE_NAMES if gender == 1 else FEMALE_NAMES)
    sur = random.choice(SURNAMES)
    return f"{base} {sur}".strip()


def jitter(latlon, km):
    lat, lon = latlon
    return (round(lat + random.uniform(-km, km) / 111.0, 6),
            round(lon + random.uniform(-km, km) / 111.0, 6))


def brief_facts(subhead_id, crime_name, place):
    tmpl = random.choice(MO_TEMPLATES.get(subhead_id, [GENERIC_MO]))
    return tmpl.format(
        crime=crime_name.lower(), place=place,
        bike=random.choice(["black Pulsar motorcycle", "red Activa scooter", "blue Splendor"]),
        wt=random.choice([15, 20, 25, 30, 40]),
        dirn=random.choice(["Majestic", "Market side", "ring road"]),
        time=f"{random.randint(6,22):02d}{random.choice(['00','30'])}",
        time2=f"{random.randint(6,22):02d}00",
        vehicle=random.choice(["Splendor motorcycle", "Activa scooter", "Swift car", "Bolero"]),
        rto=random.choice(["01","02","03","04","05","41","51"]),
        plate=f"{random.choice('ABCDEFGH')}{random.choice('ABCDEFGH')}-{random.randint(1000,9999)}",
        daynight=random.choice(["night hours", "daytime when occupants were away"]),
        amt=random.choice([25000, 50000, 80000, 120000, 200000, 350000]),
        scheme=random.choice(["investment", "part-time job", "loan app", "KYC update"]),
        motive=random.choice(["land dispute", "money matter", "old rivalry", "trivial issue"]),
        weapon=random.choice(["club", "knife", "iron rod", "stone"]),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firs", type=int, default=50000)
    ap.add_argument("--out", default="./data")
    ap.add_argument("--start-year", type=int, default=2021)
    ap.add_argument("--end-year", type=int, default=2026)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    def write(name, header, rows):
        with open(os.path.join(args.out, f"{name}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        print(f"  {name}.csv: {len(rows)} rows")

    # ---------------- lookups ----------------
    write("State", ["StateID","StateName","NationalityID","Active"],
          [(29, "Karnataka", 1, 1), (28, "Andhra Pradesh", 1, 1),
           (33, "Tamil Nadu", 1, 1), (32, "Kerala", 1, 1), (27, "Maharashtra", 1, 1)])
    write("District", ["DistrictID","DistrictName","StateID","Active"],
          [(d, n, 29, 1) for d, n in DISTRICTS])
    write("UnitType", ["UnitTypeID","UnitTypeName","CityDistState","Hierarchy","Active"],
          [(1, "Police Station", "City", 5, 1), (2, "Circle Office", "City", 4, 1),
           (3, "Sub-Division", "District", 3, 1), (4, "District Office", "District", 2, 1)])
    write("Unit", ["UnitID","UnitName","TypeID","ParentUnit","NationalityID","StateID","DistrictID","Active"],
          [(1, "Bengaluru City Commissionerate", 4, "", 1, 29, 1, 1)] +
          [(uid, name, 1, 1, 1, 29, dist, 1) for uid, name, dist, _, _ in STATIONS])
    write("Rank", ["RankID","RankName","Hierarchy","Active"], [(r, n, h, 1) for r, n, h in RANKS])
    write("Designation", ["DesignationID","DesignationName","Active","SortOrder"],
          [(d, n, 1, s) for d, n, s in DESIGNATIONS])
    write("CaseCategory", ["CaseCategoryID","LookupValue"],
          [(1, "FIR"), (3, "UDR"), (4, "PAR"), (8, "Zero FIR")])
    write("GravityOffence", ["GravityOffenceID","LookupValue"],
          [(1, "Heinous"), (2, "Non-Heinous")])
    write("CrimeHead", ["CrimeHeadID","CrimeGroupName","Active"],
          [(h, n, 1) for h, n in CRIME_HEADS])
    write("CrimeSubHead", ["CrimeSubHeadID","CrimeHeadID","CrimeHeadName","SeqID"],
          [(s, h, n, i+1) for i, (s, h, n, _, _) in enumerate(CRIME_SUBHEADS)])
    write("Act", ["ActCode","ActDescription","ShortName","Active"],
          [(c, d, s, 1) for c, d, s in ACTS])
    sections = sorted({(a, s) for pairs in SUBHEAD_SECTIONS.values() for a, s in pairs})
    write("Section", ["ActCode","SectionCode","SectionDescription","Active"],
          [(a, s, f"Section {s} of {a}", 1) for a, s in sections])
    write("CrimeHeadActSection", ["CrimeHeadID","ActCode","SectionCode"],
          [(next(h for sid2, h, _, _, _ in CRIME_SUBHEADS if sid2 == sid), a, s)
           for sid, pairs in SUBHEAD_SECTIONS.items() for a, s in pairs])
    write("CaseStatusMaster", ["CaseStatusID","CaseStatusName"], CASE_STATUSES)
    write("CasteMaster", ["caste_master_id","caste_master_name"], CASTES)
    write("ReligionMaster", ["ReligionID","ReligionName"], RELIGIONS)
    write("OccupationMaster", ["OccupationID","OccupationName"], OCCUPATIONS)

    # ---------------- employees ----------------
    employees = []
    emp_by_station = {}
    eid = 1000
    for uid, _, dist, _, _ in STATIONS:
        emp_by_station[uid] = []
        for k in range(random.randint(6, 10)):
            eid += 1
            rank = random.choices([4, 5, 6, 7], weights=[1, 2, 3, 4])[0]
            desig = 1 if k == 0 else (2 if rank in (4, 5) else random.choice([3, 4]))
            employees.append((eid, dist, uid, rank, desig, f"KGID{eid}",
                              pick_name(1), date(1975 + random.randint(0, 25),
                              random.randint(1, 12), random.randint(1, 28)).isoformat(),
                              1, random.randint(1, 8), 0,
                              date(2000 + random.randint(0, 22), random.randint(1, 12), 1).isoformat()))
            emp_by_station[uid].append(eid)
    write("Employee", ["EmployeeID","DistrictID","UnitID","RankID","DesignationID","KGID",
                       "FirstName","EmployeeDOB","GenderID","BloodGroupID",
                       "PhysicallyChallenged","AppointmentDate"], employees)

    courts = [(c, f"{n} District & Sessions Court", d, 29, 1)
              for c, (d, n) in enumerate(DISTRICTS, start=1)]
    write("Court", ["CourtID","CourtName","DistrictID","StateID","Active"], courts)
    court_by_district = {d: c for c, (d, n) in enumerate(DISTRICTS, start=1)}

    # ---------------- FIR generation ----------------
    station_meta = {uid: (dist, lat, lon) for uid, _, dist, lat, lon in STATIONS}
    serials = {}          # (station, category, year) -> serial
    subhead_weights = [w for _, _, _, w, _ in CRIME_SUBHEADS]
    subhead_by_id = {s: (h, n, g) for s, h, n, _, g in CRIME_SUBHEADS}

    case_rows, occ_rows, comp_rows, vic_rows, acc_rows = [], [], [], [], []
    asa_rows, arr_rows, arr_junction, cs_rows = [], [], [], []
    cid = vid = aid = compid = arrid = csid = 0

    n_p1 = 12                       # repeat-offender cases
    n_p2 = 15                       # gang cases
    total = args.firs
    years = list(range(args.start_year, args.end_year + 1))

    def new_case(reg_date, station, subhead_id, facts_place=None, latlon=None,
                 accused_list=None, io_override=None, force_status=None):
        """accused_list: list of (name, birth_year, gender)"""
        nonlocal cid, vid, aid, compid, arrid, csid
        cid += 1
        dist, slat, slon = station_meta[station]
        head, crime_name, gravity = subhead_by_id[subhead_id]
        year = reg_date.year
        key = (station, 1, year)
        serials[key] = serials.get(key, 0) + 1
        crime_no = f"1{dist:04d}{station:04d}{year}{serials[key]:05d}"
        case_no = f"{year}{serials[key]:05d}"
        status = force_status or random.choices([1, 2, 3, 4, 5, 6],
                                                weights=[30, 25, 4, 20, 12, 9])[0]
        io = io_override or random.choice(emp_by_station[station])
        case_rows.append((cid, crime_no, case_no, reg_date.isoformat(), io, station,
                          1, gravity, head, subhead_id, status, court_by_district[dist]))

        place = facts_place or random.choice(PLACES)
        lat, lon = latlon or jitter((slat, slon), 3.0)
        inc_from = datetime.combine(reg_date - timedelta(days=random.randint(0, 2)),
                                    datetime.min.time()) + timedelta(hours=random.randint(0, 23))
        occ_rows.append((cid, inc_from.isoformat(sep=" "),
                         (inc_from + timedelta(hours=random.randint(0, 6))).isoformat(sep=" "),
                         (inc_from + timedelta(hours=random.randint(1, 48))).isoformat(sep=" "),
                         lat, lon, brief_facts(subhead_id, crime_name, place)))

        compid += 1
        cg = random.choices([1, 2], weights=[6, 4])[0]
        comp_rows.append((compid, cid, pick_name(cg), random.randint(18, 70),
                          random.choices(range(1, 11), weights=[8,20,8,12,10,15,8,12,5,2])[0],
                          random.choices(range(1, 8), weights=[70,15,8,3,1,1,2])[0],
                          random.randint(1, 10), cg))

        if subhead_id in (101, 102, 103, 104, 203, 204, 301, 302):
            vid += 1
            vg = 2 if subhead_id in (203, 301, 302) else random.choice([1, 2])
            vic_rows.append((vid, cid, pick_name(vg), random.randint(16, 75), vg,
                             "1" if random.random() < 0.01 else "0"))

        accused_here = []
        if accused_list is None:
            n_acc = 0 if (subhead_id in (201, 202, 205, 501, 502) and random.random() < 0.55) \
                    else random.choices([1, 2, 3], weights=[6, 3, 1])[0]
            accused_list = [(pick_name(1), year - random.randint(18, 50), 1)
                            for _ in range(n_acc)]
        for i, (aname, byear, agender) in enumerate(accused_list, start=1):
            aid += 1
            acc_rows.append((aid, cid, aname, year - byear, agender, f"A{i}"))
            accused_here.append(aid)

        for j, (act, sec) in enumerate(SUBHEAD_SECTIONS[subhead_id], start=1):
            asa_rows.append((cid, act, sec, 1, j))

        # arrests for ~60% of cases that have accused
        if accused_here and random.random() < 0.6:
            arrid += 1
            adate = reg_date + timedelta(days=random.randint(1, 90))
            arr_rows.append((arrid, cid, random.choices([1, 2], weights=[9, 1])[0],
                             adate.isoformat(), 29, dist, station, io,
                             court_by_district[dist], accused_here[0], 1, 0))
            for a in accused_here:
                arr_junction.append((arrid, a))

        # chargesheet for resolved statuses
        if status in (2, 3, 4, 5, 6):
            csid += 1
            cstype = {3: "B", 4: "C"}.get(status, "A")
            # P5: this IO closes an anomalous share as false cases
            if io == P5_IO_ID and random.random() < 0.35:
                cstype = "B"
            cs_rows.append((csid, cid,
                            (reg_date + timedelta(days=random.randint(30, 300))).isoformat() + " 00:00:00",
                            cstype, io))
        return cid

    print(f"Generating {total} FIRs...")

    # P1 — repeat offender across stations, name drifts
    for i in range(n_p1):
        y = random.choice(years)
        d = date(y, random.randint(1, 12), random.randint(1, 28))
        st = random.choice([6, 7, 8, 9, 10])
        new_case(d, st, random.choice([201, 202, 205]),
                 accused_list=[(random.choice(P1_VARIANTS), P1_BIRTH_YEAR + random.choice([-1, 0, 0, 1]), 1)])

    # P2 — gang chain-snatching, co-occurring accused, Bengaluru
    for i in range(n_p2):
        y = random.choice(years[2:])
        d = date(y, random.randint(1, 12), random.randint(1, 28))
        st = random.choice([6, 7, 8])
        members = random.sample(P2_GANG, random.randint(2, 4))
        new_case(d, st, 203, latlon=jitter(P3_CENTRE, 2.5),
                 accused_list=[(n, by, 1) for n, by in members])

    # bulk
    remaining = total - n_p1 - n_p2
    for i in range(remaining):
        y = random.choice(years)
        m = random.randint(1, 12)
        subhead = random.choices([s for s, *_ in CRIME_SUBHEADS], weights=subhead_weights)[0]
        # P4: burglary festival spike Oct-Dec
        if subhead == 201 and m not in (10, 11, 12) and random.random() < 0.45:
            m = random.choice([10, 11, 12])
        d = date(y, m, random.randint(1, 28))
        st = random.choice(list(station_meta.keys()))
        latlon = None
        # P3: vehicle thefts cluster near Majestic when in central stations
        if subhead == 202 and st in (6, 7, 8) and random.random() < 0.7:
            latlon = jitter(P3_CENTRE, 1.2)
        new_case(d, st, subhead, latlon=latlon)
        if (i + 1) % 10000 == 0:
            print(f"  ...{i + 1}")

    write("CaseMaster", ["CaseMasterID","CrimeNo","CaseNo","CrimeRegisteredDate",
                         "PolicePersonID","PoliceStationID","CaseCategoryID",
                         "GravityOffenceID","CrimeMajorHeadID","CrimeMinorHeadID",
                         "CaseStatusID","CourtID"], case_rows)
    write("Inv_OccuranceTime", ["CaseMasterID","IncidentFromDate","IncidentToDate",
                                "InfoReceivedPSDate","latitude","longitude","BriefFacts"], occ_rows)
    write("ComplainantDetails", ["ComplainantID","CaseMasterID","ComplainantName","AgeYear",
                                 "OccupationID","ReligionID","CasteID","GenderID"], comp_rows)
    write("Victim", ["VictimMasterID","CaseMasterID","VictimName","AgeYear","GenderID","VictimPolice"], vic_rows)
    write("Accused", ["AccusedMasterID","CaseMasterID","AccusedName","AgeYear","GenderID","PersonID"], acc_rows)
    write("ActSectionAssociation", ["CaseMasterID","ActID","SectionID","ActOrderID","SectionOrderID"], asa_rows)
    write("ArrestSurrender", ["ArrestSurrenderID","CaseMasterID","ArrestSurrenderTypeID",
                              "ArrestSurrenderDate","ArrestSurrenderStateId",
                              "ArrestSurrenderDistrictId","PoliceStationID","IOID",
                              "CourtID","AccusedMasterID","IsAccused","IsComplainantAccused"], arr_rows)
    write("inv_arrestsurrenderaccused", ["ArrestSurrenderID","AccusedMasterID"], arr_junction)
    write("ChargesheetDetails", ["CSID","CaseMasterID","csdate","cstype","PolicePersonID"], cs_rows)

    print(f"\nDone. {cid} cases, {aid} accused, {vid} victims -> {args.out}/")
    print("Planted patterns: P1 repeat offender (Manjunath* variants, b.~1992), "
          "P2 chain-snatching gang (6 members), P3 Majestic vehicle-theft hotspot, "
          "P4 Oct-Dec burglary spike, P5 IO 1007 high false-case rate.")


if __name__ == "__main__":
    main()
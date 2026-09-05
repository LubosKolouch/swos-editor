#!/usr/bin/env python3
"""
Save & Player Editor for Sensible World of Soccer (SWOS 96/97 PC DOS).
Supports direct editing of all 80 league/country files (DATA/TEAM.*) and career saves (*.CAR).
Provides sorting, searching, inline editing of skills (Passing, Shooting, Heading,
Tackling, Control, Speed, Finishing), shirt numbers, names, and positions.
"""

import os
import shutil
import json
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import webbrowser
import glob

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "DATA")


def set_base_dir(custom_path):
    """Sets the root SWOS directory and corresponding DATA folder."""
    global BASE_DIR, DATA_DIR
    BASE_DIR = os.path.abspath(custom_path)
    DATA_DIR = os.path.join(BASE_DIR, "DATA")


POSITIONS = {
    0x00: "GK",
    0x20: "RB",
    0x40: "LB",
    0x60: "D",
    0x80: "RW",
    0xA0: "LW",
    0xC0: "M",
    0xE0: "A",
}

POS_NAMES = {
    "GK": "Brankář (GK)",
    "RB": "Pravý obránce (RB)",
    "LB": "Levý obránce (LB)",
    "D": "Střední obránce (D)",
    "RW": "Pravé křídlo (RW)",
    "LW": "Levé křídlo (LW)",
    "M": "Záložník (M)",
    "A": "Útočník (A)",
}

SKILLS_DEF = [
    ("passing", "Přihrávka (Passing)", 0),
    ("shooting", "Střelba (Shooting)", 1),
    ("heading", "Hlavička (Heading)", 2),
    ("tackling", "Odebírání míče (Tackling)", 3),
    ("control", "Kontrola míče (Control)", 4),
    ("speed", "Rychlost (Speed)", 5),
    ("finishing", "Zakončení (Finishing)", 6),
]


def decode_skills(b4):
    """Unpacks 4 bytes into 7 skill values (official SWOS 0-7 scale)."""
    p = b4[0] & 0x07
    sh = (b4[1] >> 4) & 0x07
    h = b4[1] & 0x07
    t = (b4[2] >> 4) & 0x07
    c = b4[2] & 0x07
    s = (b4[3] >> 4) & 0x07
    f = b4[3] & 0x07
    return [p, sh, h, t, c, s, f]


def encode_skills(skills):
    """Packs 7 skill values (0-7 scale) into 4 bytes."""
    vals = [max(0, min(7, int(v))) for v in skills]
    while len(vals) < 7:
        vals.append(0)
    p, sh, h, t, c, s, f = vals[:7]
    b0 = p & 0x07
    b1 = ((sh & 0x07) << 4) | (h & 0x07)
    b2 = ((t & 0x07) << 4) | (c & 0x07)
    b3 = ((s & 0x07) << 4) | (f & 0x07)
    return bytes([b0, b1, b2, b3])


def list_databases():
    """Discovers all available league databases (DATA/TEAM.*) and saved careers (*.CAR)."""
    dbs = []
    # 1. League files in DATA/TEAM.*
    team_files = sorted(glob.glob(os.path.join(DATA_DIR, "TEAM.*")))
    for f in team_files:
        fname = os.path.basename(f)
        try:
            with open(f, "rb") as fp:
                cnt = int.from_bytes(fp.read(2), "big")
                sample_name = ""
                if cnt > 0:
                    t = fp.read(684)
                    sample_name = (
                        t[5:23]
                        .split(b"\x00")[0]
                        .decode("latin1", errors="ignore")
                        .strip()
                    )
                dbs.append(
                    {
                        "id": fname,
                        "name": f"Liga / Země: {fname} ({cnt} týmů, např. {sample_name})",
                        "path": f,
                        "type": "team",
                        "count": cnt,
                        "sample": sample_name,
                    }
                )
        except Exception:
            pass

    # 2. Saved careers (*.CAR)
    car_files = sorted(
        glob.glob(os.path.join(BASE_DIR, "*.CAR"))
        + glob.glob(os.path.join(BASE_DIR, "*.car"))
    )
    for f in car_files:
        fname = os.path.basename(f)
        dbs.append(
            {
                "id": fname,
                "name": f"💾 Uložená kariéra: {fname}",
                "path": f,
                "type": "career",
                "count": 0,
            }
        )
    return dbs


def backup_file(filepath):
    """Creates a safety backup copy of the file if one does not already exist."""
    bak = filepath + ".bak"
    if not os.path.exists(bak) and os.path.exists(filepath):
        shutil.copy2(filepath, bak)
        print(f"[Backup] Created safety backup: {bak}")


def load_players_from_db(db_id):
    """Loads teams and players from the specified database or career file."""
    # Prevent path traversal
    clean_id = os.path.basename(db_id)
    if clean_id.upper().endswith(".CAR"):
        filepath = os.path.join(BASE_DIR, clean_id)
        is_career = True
    else:
        filepath = os.path.join(DATA_DIR, clean_id)
        is_career = False

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File {filepath} not found!")

    with open(filepath, "rb") as f:
        data = f.read()

    teams = []
    players = []

    if not is_career:
        # Standard TEAM.XXX league file
        num_teams = int.from_bytes(data[:2], "big")
        for t_idx in range(num_teams):
            t_offset = 2 + t_idx * 684
            if t_offset + 684 > len(data):
                break
            t_data = data[t_offset : t_offset + 684]
            t_name = (
                t_data[5:23].split(b"\x00")[0].decode("latin1", errors="ignore").strip()
            )
            teams.append({"id": t_idx, "name": t_name})

            for p_idx in range(16):
                p_offset = t_offset + 76 + p_idx * 38
                p_data = t_data[76 + p_idx * 38 : 76 + (p_idx + 1) * 38]
                num = p_data[2]
                name = (
                    p_data[3:24]
                    .split(b"\x00")[0]
                    .decode("latin1", errors="ignore")
                    .strip()
                )
                pos_code = p_data[26] & 0xE0
                pos = POSITIONS.get(pos_code, "M")
                is_gk = pos == "GK"
                skills = decode_skills(p_data[28:32])
                price_code = p_data[32]
                overall = sum(skills) if not is_gk else price_code

                status_code = p_data[27]
                fitness_val = p_data[37]
                fitness_pct = round((fitness_val / 255.0) * 100) if is_career else 100

                # Status evaluation (injuries / bans from byte 27)
                status_text = "Fit"
                status_type = "ok"
                if is_career:
                    if (status_code & 0x20) or (status_code & 0x40):
                        status_text = "🚑 Injured"
                        status_type = "injured"
                    elif (status_code & 0x80) or (status_code & 0x08):
                        status_text = "🟥 Banned"
                        status_type = "banned"
                    elif status_code & 0x04:
                        status_text = "🟨 Yellow Card"
                        status_type = "warning"
                    elif fitness_pct < 60:
                        status_text = "⚠️ Tired"
                        status_type = "tired"

                players.append(
                    {
                        "id": len(players),
                        "file_offset": p_offset,
                        "team_id": t_idx,
                        "team_name": t_name,
                        "player_index": p_idx,
                        "number": num,
                        "name": name,
                        "position": pos,
                        "pos_code": pos_code,
                        "is_gk": is_gk,
                        "skills": skills,
                        "price_code": price_code,
                        "overall": overall,
                        "status_code": status_code,
                        "status_text": status_text,
                        "status_type": status_type,
                        "fitness": fitness_pct,
                        "fitness_raw": fitness_val,
                        "is_active_team": False,
                    }
                )
    else:
        # Career save file (.CAR)
        # 1. The manager's active squad (with actual transfers) is stored in the career block at offset 56192!
        # 2. Other league / cup teams start at offset 2 (slot 0 was the original pre-career template).
        num_teams = int.from_bytes(data[:2], "little")
        if num_teams <= 0 or num_teams > 200:
            num_teams = 80

        # First load the manager's active team at offset 56192
        act_offset = 56192
        if act_offset + 684 <= len(data):
            act_data = data[act_offset : act_offset + 684]
            act_t_name = (
                act_data[5:23]
                .split(b"\x00")[0]
                .decode("latin1", errors="ignore")
                .strip()
            )
            act_coach = (
                act_data[36:60]
                .split(b"\x00")[0]
                .decode("latin1", errors="ignore")
                .strip()
            )
            coach_str = f" (manager: {act_coach})" if act_coach else ""
            display_name = f"⭐ {act_t_name}{coach_str} [YOUR TEAM]"

            teams.append(
                {
                    "id": 0,
                    "name": display_name,
                    "raw_name": act_t_name,
                    "coach": act_coach,
                    "offset": act_offset,
                    "is_active": True,
                }
            )

            # In SWOS, the manager's club uses a 30-byte index table at offset 56160..56189
            # pointing to the active squad slots (0..29), with 0xFF (255) for empty slots.
            squad_indices = []
            if len(data) >= 56190:
                squad_indices = list(data[56160:56190])

            # First add players in the exact order of the active squad table
            loaded_slots = set()
            for squad_pos, s_idx in enumerate(squad_indices):
                if s_idx == 255 or s_idx >= 30:
                    continue
                loaded_slots.add(s_idx)
                p_offset = act_offset + 76 + s_idx * 38
                if p_offset + 38 > len(data):
                    break
                p_data = data[p_offset : p_offset + 38]
                num = p_data[2]
                name = (
                    p_data[3:24]
                    .split(b"\x00")[0]
                    .decode("latin1", errors="ignore")
                    .strip()
                )

                if not name or len(name) < 2 or not any(c.isalpha() for c in name):
                    continue

                pos_code = p_data[26] & 0xE0
                pos = POSITIONS.get(pos_code, "M")
                is_gk = pos == "GK"
                skills = decode_skills(p_data[28:32])
                price_code = p_data[32]
                overall = sum(skills) if not is_gk else price_code

                status_code = p_data[27]
                fitness_val = p_data[37]
                fitness_pct = round((fitness_val / 255.0) * 100)

                status_text = "Fit"
                status_type = "ok"
                if (status_code & 0x20) or (status_code & 0x40):
                    status_text = "🚑 Injured"
                    status_type = "injured"
                elif (status_code & 0x80) or (status_code & 0x08):
                    status_text = "🟥 Banned"
                    status_type = "banned"
                elif status_code & 0x04:
                    status_text = "🟨 Yellow Card"
                    status_type = "warning"
                elif fitness_pct < 60:
                    status_text = "⚠️ Tired"
                    status_type = "tired"

                players.append(
                    {
                        "id": len(players),
                        "file_offset": p_offset,
                        "team_id": 0,
                        "team_name": act_t_name,
                        "player_index": s_idx,
                        "squad_position": squad_pos,
                        "number": num,
                        "name": name,
                        "position": pos,
                        "pos_code": pos_code,
                        "is_gk": is_gk,
                        "skills": skills,
                        "price_code": price_code,
                        "overall": overall,
                        "status_code": status_code,
                        "status_text": status_text,
                        "status_type": status_type,
                        "fitness": fitness_pct,
                        "fitness_raw": fitness_val,
                        "is_active_team": True,
                    }
                )

            # Also load any remaining valid players in slots 0..29 not yet in squad_indices
            for s_idx in range(30):
                if s_idx in loaded_slots:
                    continue
                p_offset = act_offset + 76 + s_idx * 38
                if p_offset + 38 > len(data):
                    break
                p_data = data[p_offset : p_offset + 38]
                name = (
                    p_data[3:24]
                    .split(b"\x00")[0]
                    .decode("latin1", errors="ignore")
                    .strip()
                )
                if (
                    not name
                    or len(name) < 2
                    or not any(c.isalpha() for c in name)
                    or name.startswith("*ERR*")
                ):
                    continue

                pos_code = p_data[26] & 0xE0
                pos = POSITIONS.get(pos_code, "M")
                is_gk = pos == "GK"
                skills = decode_skills(p_data[28:32])
                price_code = p_data[32]
                overall = sum(skills) if not is_gk else price_code
                status_code = p_data[27]
                fitness_val = p_data[37]
                fitness_pct = round((fitness_val / 255.0) * 100)

                players.append(
                    {
                        "id": len(players),
                        "file_offset": p_offset,
                        "team_id": 0,
                        "team_name": act_t_name,
                        "player_index": s_idx,
                        "squad_position": None,
                        "number": p_data[2],
                        "name": name,
                        "position": pos,
                        "pos_code": pos_code,
                        "is_gk": is_gk,
                        "skills": skills,
                        "price_code": price_code,
                        "overall": overall,
                        "status_code": status_code,
                        "status_text": "Fit",
                        "status_type": "ok",
                        "fitness": fitness_pct,
                        "fitness_raw": fitness_val,
                        "is_active_team": True,
                    }
                )

        # Next load opponent teams (teams 1 to num_teams-1) from the league section
        for t_idx in range(1, num_teams):
            t_offset = 2 + t_idx * 684
            if t_offset + 684 > len(data):
                break
            t_data = data[t_offset : t_offset + 684]
            t_name = (
                t_data[5:23].split(b"\x00")[0].decode("latin1", errors="ignore").strip()
            )
            coach = (
                t_data[36:60]
                .split(b"\x00")[0]
                .decode("latin1", errors="ignore")
                .strip()
            )
            coach_str = f" (manager: {coach})" if coach else ""
            display_name = f"{t_name}{coach_str}"

            teams.append(
                {
                    "id": len(teams),
                    "name": display_name,
                    "raw_name": t_name,
                    "offset": t_offset,
                    "is_active": False,
                }
            )

            for p_idx in range(16):
                p_offset = t_offset + 76 + p_idx * 38
                p_data = t_data[76 + p_idx * 38 : 76 + (p_idx + 1) * 38]
                num = p_data[2]
                name = (
                    p_data[3:24]
                    .split(b"\x00")[0]
                    .decode("latin1", errors="ignore")
                    .strip()
                )
                pos_code = p_data[26] & 0xE0
                pos = POSITIONS.get(pos_code, "M")
                is_gk = pos == "GK"
                skills = decode_skills(p_data[28:32])
                price_code = p_data[32]
                overall = sum(skills) if not is_gk else price_code

                status_code = p_data[27]
                fitness_val = p_data[37]
                fitness_pct = round((fitness_val / 255.0) * 100)

                status_text = "V pořádku"
                status_type = "ok"
                if (status_code & 0x20) or (status_code & 0x40):
                    status_text = "🚑 Zraněn"
                    status_type = "injured"
                elif (status_code & 0x80) or (status_code & 0x08):
                    status_text = "🟥 Trest (Stop)"
                    status_type = "banned"
                elif status_code & 0x04:
                    status_text = "🟨 Žlutá karta"
                    status_type = "warning"
                elif fitness_pct < 60:
                    status_text = "⚠️ Vyčerpán"
                    status_type = "tired"

                players.append(
                    {
                        "id": len(players),
                        "file_offset": p_offset,
                        "team_id": len(teams) - 1,
                        "team_name": t_name,
                        "player_index": p_idx,
                        "number": num,
                        "name": name,
                        "position": pos,
                        "pos_code": pos_code,
                        "is_gk": is_gk,
                        "skills": skills,
                        "price_code": price_code,
                        "overall": overall,
                        "status_code": status_code,
                        "status_text": status_text,
                        "status_type": status_type,
                        "fitness": fitness_pct,
                        "fitness_raw": fitness_val,
                        "is_active_team": False,
                    }
                )

    return teams, players


def sync_career_active_team(fp):
    """
    Synchronizes the manager's active squad in career mode:
    Copies the top 16 match players from the active squad (56192 + 76 + slot*38)
    according to the 30-byte squad index table (56160..56189) into the domestic
    league team template at offset 2 + 76..
    """
    fp.seek(56160)
    squad = list(fp.read(30))
    for pos in range(16):
        if pos < len(squad):
            s = squad[pos]
            if s != 255 and s < 30:
                src_off = 56192 + 76 + s * 38
                dst_off = 2 + 76 + pos * 38
                fp.seek(src_off)
                rec = fp.read(38)
                fp.seek(dst_off)
                fp.write(rec)


def transfer_player(db_id, src_player_offset, target_team_id):
    """
    Executes a player transfer to the target team.
    In SWOS, each team has fixed allocated player slots (16 players in leagues TEAM.*
    and opponent teams in .CAR; up to 30 in user team).
    The transfer is carried out by swapping the 38-byte player record with a slot in the target team
    (preferring the same tactical position, or the last available slot).
    """
    clean_id = os.path.basename(db_id)
    if clean_id.upper().endswith(".CAR"):
        filepath = os.path.join(BASE_DIR, clean_id)
        is_career = True
    else:
        filepath = os.path.join(DATA_DIR, clean_id)
        is_career = False

    backup_file(filepath)
    _, players = load_players_from_db(db_id)

    # Locate the source player
    src_player = next(
        (p for p in players if p["file_offset"] == src_player_offset), None
    )
    if not src_player:
        raise ValueError(f"Source player at offset {src_player_offset} not found!")

    if src_player["team_id"] == target_team_id:
        return True  # No change required

    # Locate target team players
    target_players = [p for p in players if p["team_id"] == target_team_id]
    if not target_players:
        raise ValueError(f"Target team #{target_team_id} has no players!")

    # Find the most suitable player slot to swap: same position, or fallback to the last player
    same_pos = [p for p in target_players if p["position"] == src_player["position"]]
    swap_target = same_pos[0] if same_pos else target_players[-1]
    dst_offset = swap_target["file_offset"]

    with open(filepath, "r+b") as fp:
        fp.seek(src_player_offset)
        rec_src = fp.read(38)
        fp.seek(dst_offset)
        rec_dst = fp.read(38)

        # Swap records
        fp.seek(src_player_offset)
        fp.write(rec_dst)
        fp.seek(dst_offset)
        fp.write(rec_src)

        # Synchronization for career mode active team
        if is_career:
            # Ensure squad index table includes the target slot if transferred into active team
            act_start = 56192 + 76
            act_end = act_start + 30 * 38
            if act_start <= dst_offset < act_end:
                slot_idx = (dst_offset - act_start) // 38
                fp.seek(56160)
                squad = bytearray(fp.read(30))
                if slot_idx not in squad:
                    # Place into first empty (255) squad position
                    for idx, val in enumerate(squad):
                        if val == 255:
                            squad[idx] = slot_idx
                            break
                    fp.seek(56160)
                    fp.write(squad)

            sync_career_active_team(fp)

    return True


def save_player(db_id, player_offset, data):
    """Writes player modifications directly to the file offset (including any transfer)."""
    clean_id = os.path.basename(db_id)
    if clean_id.upper().endswith(".CAR"):
        filepath = os.path.join(BASE_DIR, clean_id)
        is_career = True
    else:
        filepath = os.path.join(DATA_DIR, clean_id)
        is_career = False

    backup_file(filepath)

    with open(filepath, "r+b") as fp:
        fp.seek(player_offset)
        rec = bytearray(fp.read(38))

        if "number" in data:
            num = max(1, min(99, int(data["number"])))
            rec[2] = num & 0xFF

        if "name" in data:
            raw_name = str(data["name"]).strip().encode("latin1", errors="ignore")[:20]
            rec[3:24] = raw_name.ljust(21, b"\x00")

        if "position" in data:
            pos_str = str(data["position"]).strip().upper()
            pos_lookup = {v: k for k, v in POSITIONS.items()}
            if pos_str in pos_lookup:
                # Preserve lower 5 bits (skin/hair color attributes)
                rec[26] = (rec[26] & 0x1F) | pos_lookup[pos_str]

        if "price_code" in data:
            price = max(0, min(255, int(data["price_code"])))
            rec[32] = price & 0xFF

        if "skills" in data:
            new_skills_bytes = encode_skills(data["skills"])
            rec[28:32] = new_skills_bytes

        if "fitness" in data:
            fit_pct = max(0, min(100, int(data["fitness"])))
            rec[37] = round((fit_pct / 100.0) * 255)

        if "status_code" in data:
            sc = max(0, min(255, int(data["status_code"])))
            rec[27] = sc & 0xFF

        fp.seek(player_offset)
        fp.write(rec)

        if is_career:
            sync_career_active_team(fp)

    # Handle transfer to another team if requested
    if "target_team_id" in data and data["target_team_id"] is not None:
        target_team_id = int(data["target_team_id"])
        transfer_player(db_id, player_offset, target_team_id)

    return True


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SWOS 96/97 - Player & Career Editor</title>
<style>
  :root {
    --bg-main: #0a1118;
    --bg-card: #121e2b;
    --bg-header: #0e1824;
    --accent: #10b981;
    --accent-hover: #059669;
    --accent-blue: #0284c7;
    --text-main: #f0fdf4;
    --text-muted: #94a3b8;
    --border: #1e3144;
    --danger: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { background: var(--bg-main); color: var(--text-main); display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

  /* Top Navigation Bar */
  .top-nav { background: var(--bg-header); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 10px 24px; gap: 16px; flex-wrap: wrap; z-index: 10; }
  .nav-left { display: flex; align-items: center; gap: 16px; }
  .nav-left h1 { font-size: 1.2rem; font-weight: 700; color: var(--accent); letter-spacing: 0.5px; white-space: nowrap; }

  .db-select-wrap { display: flex; align-items: center; gap: 8px; background: #162638; padding: 5px 12px; border-radius: 6px; border: 1px solid var(--border); }
  .db-select-wrap label { font-size: 0.8rem; font-weight: 600; color: var(--accent); white-space: nowrap; }
  .db-select-wrap select { background: transparent; border: none; color: #fff; font-size: 0.88rem; font-weight: 600; outline: none; cursor: pointer; max-width: 420px; }
  .db-select-wrap select option { background: #121e2b; color: #fff; }

  .table-controls { padding: 10px 24px; background: var(--bg-header); border-bottom: 1px solid var(--border); display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
  .table-controls input, .table-controls select { width: auto; min-width: 170px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 7px 10px; color: var(--text-main); font-size: 0.88rem; outline: none; }
  .table-controls input:focus, .table-controls select:focus { border-color: var(--accent); }

  .table-scroll { flex: 1; overflow: auto; }
  table.skill-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; text-align: center; }
  table.skill-table thead { position: sticky; top: 0; background: #0c1622; z-index: 5; }
  table.skill-table th { padding: 10px 8px; border-bottom: 2px solid var(--border); color: var(--text-muted); font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; transition: background 0.15s; }
  table.skill-table th:hover { background: #162638; color: var(--accent); }
  table.skill-table th.active-sort { color: var(--accent); background: #142334; border-bottom: 2px solid var(--accent); }
  table.skill-table th .sort-icon { font-size: 0.75rem; margin-left: 4px; }
  table.skill-table td { padding: 4px 6px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  table.skill-table tbody tr { transition: background 0.15s; }
  table.skill-table tbody tr:hover { background: #162536; }
  table.skill-table tbody tr:nth-child(even) { background: rgba(255,255,255,0.015); }
  td.cell-name { text-align: left; font-weight: 600; padding-left: 14px; color: #fff; cursor: pointer; }
  td.cell-name:hover { color: var(--accent); text-decoration: underline; }
  td.cell-team { text-align: left; color: var(--text-muted); }

  /* Inline inputs */
  input.inline-num {
    width: 44px;
    text-align: center;
    padding: 5px 2px;
    font-size: 0.9rem;
    font-weight: 700;
    border-radius: 5px;
    border: 1px solid rgba(255,255,255,0.1);
    background: #162638;
    color: #fff;
    outline: none;
    transition: all 0.15s;
    cursor: text;
  }
  input.inline-num:hover { border-color: var(--accent); background: #1b2f45; }
  input.inline-num:focus { border-color: var(--accent); background: #0c1622; color: #fff; box-shadow: 0 0 8px rgba(16,185,129,0.6); }
  input.inline-num.saved-flash, select.inline-team.saved-flash, select.inline-select-status.saved-flash { animation: flashSuccess 0.8s ease-out; }
  @keyframes flashSuccess { 0% { background: #059669; color: #fff; } 100% { background: #162638; } }

  select.inline-team {
    background: #162638;
    color: #e2e8f0;
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 0.82rem;
    font-weight: 600;
    outline: none;
    cursor: pointer;
    max-width: 220px;
    transition: all 0.15s;
  }
  select.inline-team:hover { border-color: var(--accent); background: #1b2f45; }
  select.inline-team:focus { border-color: var(--accent); background: #0c1622; box-shadow: 0 0 8px rgba(16,185,129,0.5); }
  select.inline-team option { background: #121e2b; color: #fff; }

  .p-badge { background: #064e3b; color: #6ee7b7; font-weight: 700; font-size: 0.8rem; padding: 2px 7px; border-radius: 4px; }
  .p-badge.GK { background: #1e3a5f; color: #7dd3fc; }
  .p-badge.A { background: #7f1d1d; color: #fca5a5; }

  /* Status and fitness badges */
  .status-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; white-space: nowrap; }
  .status-badge.ok { background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid rgba(16,185,129,0.3); }
  .status-badge.injured { background: rgba(239,68,68,0.2); color: #f87171; border: 1px solid rgba(239,68,68,0.4); animation: pulseInjured 2s infinite; }
  .status-badge.banned { background: rgba(220,38,38,0.25); color: #fca5a5; border: 1px solid #ef4444; }
  .status-badge.warning { background: rgba(245,158,11,0.2); color: #fbbf24; border: 1px solid rgba(245,158,11,0.4); }
  .status-badge.tired { background: rgba(234,179,8,0.15); color: #fde047; border: 1px solid rgba(234,179,8,0.3); }
  @keyframes pulseInjured { 0%, 100% { opacity: 1; } 50% { opacity: 0.65; } }

  .inline-select-status {
    background: #162638;
    color: #fff;
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 6px;
    padding: 4px 6px;
    font-size: 0.78rem;
    font-weight: 600;
    outline: none;
    cursor: pointer;
  }
  .inline-select-status.injured { background: #450a0a; color: #fca5a5; border-color: #ef4444; }
  .inline-select-status.banned { background: #581c1c; color: #fca5a5; border-color: #dc2626; }
  .inline-select-status.warning { background: #451a03; color: #fde68a; border-color: #f59e0b; }
  .inline-select-status.ok { background: #064e3b; color: #6ee7b7; border-color: #10b981; }

  input.inline-fit { width: 50px; }

  .val-high { color: #34d399 !important; }
  .val-mid { color: #facc15 !important; }
  .val-low { color: #94a3b8 !important; }

  #toast { position: fixed; bottom: 20px; right: 24px; background: #10b981; color: #fff; font-weight: 600; font-size: 0.88rem; padding: 10px 18px; border-radius: 6px; box-shadow: 0 4px 14px rgba(0,0,0,0.5); display: none; z-index: 200; }
  #toast.show { display: block; animation: slideUp 0.2s ease-out; }
  @keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

  /* MODAL */
  .modal-backdrop { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); backdrop-filter: blur(4px); z-index: 100; display: none; align-items: center; justify-content: center; }
  .modal-backdrop.show { display: flex; }
  .modal-content { background: var(--bg-card); border: 1px solid var(--accent); border-radius: 12px; width: 92%; max-width: 800px; max-height: 90vh; overflow-y: auto; padding: 26px 32px; box-shadow: 0 10px 30px rgba(0,0,0,0.6); }
  .modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 20px; }
  .modal-header h2 { font-size: 1.25rem; color: var(--accent); }
  .modal-close { background: transparent; border: none; font-size: 1.5rem; color: var(--text-muted); cursor: pointer; }
  .modal-close:hover { color: #fff; }
  .grid-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }
  .form-group { display: flex; flex-direction: column; gap: 6px; }
  .form-group label { font-size: 0.8rem; color: var(--text-muted); font-weight: 500; }
  .form-group input, .form-group select { background: #162638; border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; color: #fff; outline: none; }
  .card-title { font-size: 1rem; font-weight: 600; margin-bottom: 14px; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  .attr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 14px; }
  .attr-slider-wrap { display: flex; align-items: center; gap: 10px; }
  .attr-slider-wrap input[type=range] { flex: 1; accent-color: var(--accent); }
  .attr-val { font-weight: bold; width: 28px; text-align: right; color: var(--accent); }
  .btn-bar { display: flex; gap: 12px; justify-content: flex-end; margin-top: 20px; }
  button.btn-primary { background: linear-gradient(135deg, var(--accent), var(--accent-hover)); border: none; border-radius: 6px; padding: 10px 22px; font-size: 0.95rem; font-weight: 600; color: #fff; cursor: pointer; }
  button.btn-cancel { background: #1e3144; border: none; border-radius: 6px; padding: 10px 18px; font-size: 0.95rem; font-weight: 600; color: #cbd5e1; cursor: pointer; }

  /* Language Switcher */
  .lang-toggle { display: inline-flex; align-items: center; background: #162638; border: 1px solid var(--border); border-radius: 20px; padding: 2px; gap: 2px; }
  .lang-btn { background: transparent; border: none; color: var(--text-muted); font-size: 0.8rem; font-weight: 700; padding: 4px 10px; border-radius: 16px; cursor: pointer; transition: all 0.2s; }
  .lang-btn:hover { color: #fff; }
  .lang-btn.active { background: var(--accent); color: #fff; box-shadow: 0 0 10px rgba(16,185,129,0.4); }

  /* Unofficial Badge */
  .unofficial-badge {
    background: rgba(239, 68, 68, 0.18);
    color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.45);
    font-size: 0.68rem;
    font-weight: 800;
    letter-spacing: 0.8px;
    padding: 2px 7px;
    border-radius: 4px;
    text-transform: uppercase;
    vertical-align: middle;
    margin-left: 8px;
  }

  /* GitHub Link */
  .github-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #162638;
    color: #cbd5e1;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 0.8rem;
    font-weight: 600;
    text-decoration: none;
    transition: all 0.2s;
  }
  .github-link:hover {
    background: #1e3144;
    color: #fff;
    border-color: var(--accent);
  }
  .github-link svg {
    width: 15px;
    height: 15px;
    fill: currentColor;
  }
</style>
</head>
<body>

<div class="top-nav">
  <div class="nav-left">
    <h1 id="app-title">⚽ SWOS 96/97 Editor <span class="unofficial-badge" id="lbl-unofficial">Unofficial</span></h1>
    <div class="db-select-wrap">
      <label id="lbl-db-select">📁 Liga / Země / Kariéra:</label>
      <select id="dbSelect" onchange="onDbChange()"></select>
    </div>
  </div>
  <div style="display: flex; align-items: center; gap: 14px;">
    <div class="lang-toggle">
      <button class="lang-btn active" id="lang-btn-en" onclick="setLanguage('en')">🇬🇧 EN</button>
      <button class="lang-btn" id="lang-btn-cs" onclick="setLanguage('cs')">🇨🇿 CZ</button>
    </div>
    <span id="player-count" style="font-size:0.85rem; color:var(--text-muted);">0 players</span>
    <a href="https://github.com/LubosKolouch/swos-editor" target="_blank" rel="noopener noreferrer" class="github-link" title="GitHub Repository">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path></svg>
      <span>swos-editor</span>
    </a>
  </div>
</div>

<div id="toast"></div>

<div class="table-controls">
  <input type="text" id="tableSearchInput" placeholder="🔍 Hledat hráče..." oninput="renderTable()">
  <select id="tableTeamFilter" onchange="renderTable()">
    <option value="">-- Všechny týmy --</option>
  </select>
  <select id="tablePosFilter" onchange="renderTable()">
    <option value="">-- Všechny pozice --</option>
    <option value="GK">Pouze Brankáři (GK)</option>
    <option value="D">Obránci (D, RB, LB)</option>
    <option value="M">Záložníci (M, RW, LW)</option>
    <option value="A">Útočníci (A)</option>
  </select>
  <span style="font-size: 0.85rem; color: var(--text-muted);" id="tableCount">Zobrazeno: 0</span>
  <span id="tableHint" style="font-size: 0.82rem; color: var(--accent); margin-left: auto;">⚡ <b>Skills (0-7) and shirt (#) can be edited directly in cells + Enter.</b> Automatically saved into game!</span>
</div>

<div class="table-scroll">
  <table class="skill-table" id="skillsTable">
    <thead>
      <tr id="tableHeaders"></tr>
    </thead>
    <tbody id="tableBody"></tbody>
  </table>
</div>

<!-- MODAL PRO DETAILNÍ EDITACI HRÁČE -->
<div class="modal-backdrop" id="editModal" onclick="if(event.target === this) closeModal();">
  <div class="modal-content">
    <div class="modal-header">
      <h2 id="modalPlayerTitle">Úprava hráče</h2>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>

    <div class="grid-form">
      <div class="form-group">
        <label id="lbl_mName">Jméno hráče</label>
        <input type="text" id="mName" maxlength="20">
      </div>
      <div class="form-group">
        <label id="lbl_mNumber">Číslo dresu (#)</label>
        <input type="number" id="mNumber" min="1" max="99">
      </div>
      <div class="form-group">
        <label id="lbl_mPosition">Pozice</label>
        <select id="mPosition" onchange="onPositionChange()">
          <option value="GK">GK (Brankář)</option>
          <option value="RB">RB (Pravý obránce)</option>
          <option value="LB">LB (Levý obránce)</option>
          <option value="D">D (Střední obránce)</option>
          <option value="RW">RW (Pravé křídlo)</option>
          <option value="LW">LW (Levé křídlo)</option>
          <option value="M">M (Záložník)</option>
          <option value="A">A (Útočník)</option>
        </select>
      </div>
      <div class="form-group">
        <label id="lbl_mPrice">Cena / Hodnota (Price Code 0-63)</label>
        <input type="number" id="mPrice" min="0" max="255">
      </div>
      <div class="form-group">
        <label id="lbl_mFitness">❤️ Kondice / Fitness (0 - 100 %)</label>
        <div class="attr-slider-wrap">
          <input type="range" min="0" max="100" id="mFitness" oninput="document.getElementById('mFitnessVal').textContent = this.value + '%'">
          <span class="attr-val" id="mFitnessVal" style="width:48px;">100%</span>
        </div>
      </div>
      <div class="form-group">
        <label id="lbl_mStatus">🚑 Stav hráče (Zdraví / Trest)</label>
        <select id="mStatus">
          <option value="0">✅ V pořádku (Fit)</option>
          <option value="32">🚑 Zraněn (Injury 1)</option>
          <option value="64">🚑 Vážně zraněn (Injury 2)</option>
          <option value="4">🟨 Žlutá karta</option>
          <option value="128">🟥 Trest / Stop (Suspension)</option>
          <option value="196">🚑+🟥 Zraněn i trest</option>
        </select>
      </div>
      <div class="form-group">
        <label id="lbl_mTeam">🔄 Tým (Přestup / Výměna do jiného týmu)</label>
        <select id="mTeam"></select>
      </div>
    </div>

    <div id="modalSkillsSection">
      <div class="card-title" id="lbl_mSkillsTitle">Herní dovednosti (0 - 7)</div>
      <div class="attr-grid" id="modalAttrContainer"></div>
    </div>

    <div class="btn-bar">
      <button class="btn-cancel" id="btnCancelModal" onclick="closeModal()">Zrušit</button>
      <button class="btn-primary" id="btnSaveModal" onclick="saveModalPlayer()">💾 Uložit změny do SWOS</button>
    </div>
  </div>
</div>

<script>
let currentDbId = 'TEAM.008'; // Default: English League
let allDatabases = [];
let allTeams = [];
let allPlayers = [];
let selectedPlayer = null;

let tableSortCol = 'overall';
let tableSortAsc = false;
let currentLang = localStorage.getItem('swos_lang') || 'en';

const I18N = {
  cs: {
    appTitle: "⚽ SWOS 96/97 Editor",
    lblDbSelect: "📁 Liga / Země / Kariéra:",
    playerCount: (cnt, db) => `${cnt} hráčů (${db})`,
    tableCount: (cnt) => `Zobrazeno: ${cnt} hráčů`,
    searchPlaceholder: "🔍 Hledat hráče...",
    allTeams: "-- Všechny týmy --",
    allPositions: "-- Všechny pozice --",
    posGK: "Pouze Brankáři (GK)",
    posD: "Obránci (D, RB, LB)",
    posM: "Záložníci (M, RW, LW)",
    posA: "Útočníci (A)",
    tableHint: "⚡ <b>Dovednosti (0-7) i dres (#) lze přepsat přímo v buňce + Enter.</b> Automaticky se uloží do hry!",
    fitTooltip: "Klikněte a přepište kondici (0-100%)",
    nameTooltip: "Kliknutím otevřete kartu hráče",
    // Table cols
    colName: "Jméno hráče",
    colTeam: "🔄 Tým (Přestup)",
    colPos: "Poz",
    colNum: "#",
    colStatus: "🏥 Stav / Zranění",
    colFitness: "❤️ Kondice",
    colOverall: "⭐ Součet",
    colPassing: "Přihrávka",
    colShooting: "Střelba",
    colHeading: "Hlavička",
    colTackling: "Odebírání",
    colControl: "Kontrola",
    colSpeed: "Rychlost",
    colFinishing: "Zakončení",
    // Skills with abbreviations
    skillPassing: "Přihrávka (P)",
    skillShooting: "Střelba (Sh)",
    skillHeading: "Hlavička (H)",
    skillTackling: "Odebírání (T)",
    skillControl: "Kontrola (C)",
    skillSpeed: "Rychlost (S)",
    skillFinishing: "Zakončení (F)",
    // Status options
    statusOk: "✅ V pořádku",
    statusInjured1: "🚑 Zraněn",
    statusInjured2: "🚑 Vážně zraněn",
    statusYellow: "🟨 Žlutá karta",
    statusBanned: "🟥 Trest (Stop)",
    statusInjuredAndBanned: "🚑+🟥 Zraněn i trest",
    statusTired: "⚠️ Vyčerpán",
    // Modal
    modalEditTitle: (num, pos, name, team) => `Úprava: #${num} ${pos} - ${name} (${team})`,
    lblMName: "Jméno hráče",
    lblMNum: "Číslo dresu (#)",
    lblMPos: "Pozice",
    lblMPrice: "Cena / Hodnota (Price Code 0-63)",
    lblMFit: "❤️ Kondice / Fitness (0 - 100 %)",
    lblMStatus: "🚑 Stav hráče (Zdraví / Trest)",
    lblMTeam: "🔄 Tým (Přestup / Výměna do jiného týmu)",
    lblMSkillsTitle: "Herní dovednosti (0 - 7)",
    btnCancel: "Zrušit",
    btnSave: "💾 Uložit změny do SWOS",
    // Positions in modal
    posOptGK: "GK (Brankář)",
    posOptRB: "RB (Pravý obránce)",
    posOptLB: "LB (Levý obránce)",
    posOptD: "D (Střední obránce)",
    posOptRW: "RW (Pravé křídlo)",
    posOptLW: "LW (Levé křídlo)",
    posOptM: "M (Záložník)",
    posOptA: "A (Útočník)",
    // Toast & Alerts
    dbLoaded: (id) => `📁 Načtena databáze: ${id}`,
    transferSuccess: (name, team) => `✓ Přestup: ${name} -> ${team}`,
    transferError: (err) => `Chyba při přestupu: ${err}`,
    skillSaved: (name, skill, val) => `✓ ${name}: ${skill} = ${val}`,
    numSaved: (name, num) => `✓ ${name}: dres #${num} uložen`,
    fitnessSaved: (name, val) => `✓ ${name}: kondice nastavena na ${val}%`,
    statusSaved: (name, label) => `✓ ${name}: stav nastaven na "${label}"`,
    saveSuccessTransferred: (name, team) => `✓ Přestup & uložení: ${name} -> ${team}`,
    saveSuccess: (name) => `✓ Uložen: ${name}`,
    saveError: (err) => `Chyba při ukládání: ${err}`,
    leagueFormat: (id, cnt, sample) => `Liga / Země: ${id} (${cnt} týmů, např. ${sample})`,
    careerFormat: (id) => `💾 Uložená kariéra: ${id}`,
    yourTeamBadge: " [TVŮJ TÝM]"
  },
  en: {
    appTitle: "⚽ SWOS 96/97 Editor",
    lblDbSelect: "📁 League / Country / Career:",
    playerCount: (cnt, db) => `${cnt} players (${db})`,
    tableCount: (cnt) => `Showing: ${cnt} players`,
    searchPlaceholder: "🔍 Search players...",
    allTeams: "-- All Teams --",
    allPositions: "-- All Positions --",
    posGK: "Goalkeepers only (GK)",
    posD: "Defenders (D, RB, LB)",
    posM: "Midfielders (M, RW, LW)",
    posA: "Attackers (A)",
    tableHint: "⚡ <b>Skills (0-7) and shirt (#) can be edited directly in cells + Enter.</b> Automatically saved into game!",
    fitTooltip: "Click to edit fitness (0-100%)",
    nameTooltip: "Click to open player card",
    // Table cols
    colName: "Player Name",
    colTeam: "🔄 Team (Transfer)",
    colPos: "Pos",
    colNum: "#",
    colStatus: "🏥 Status / Injury",
    colFitness: "❤️ Fitness",
    colOverall: "⭐ Total",
    colPassing: "Passing",
    colShooting: "Shooting",
    colHeading: "Heading",
    colTackling: "Tackling",
    colControl: "Control",
    colSpeed: "Speed",
    colFinishing: "Finishing",
    // Skills with abbreviations
    skillPassing: "Passing (P)",
    skillShooting: "Shooting (Sh)",
    skillHeading: "Heading (H)",
    skillTackling: "Tackling (T)",
    skillControl: "Control (C)",
    skillSpeed: "Speed (S)",
    skillFinishing: "Finishing (F)",
    // Status options
    statusOk: "✅ Fit (Ready)",
    statusInjured1: "🚑 Injured (Minor)",
    statusInjured2: "🚑 Severely Injured",
    statusYellow: "🟨 Yellow Card",
    statusBanned: "🟥 Suspended (Ban)",
    statusInjuredAndBanned: "🚑+🟥 Injured & Banned",
    statusTired: "⚠️ Exhausted",
    // Modal
    modalEditTitle: (num, pos, name, team) => `Edit: #${num} ${pos} - ${name} (${team})`,
    lblMName: "Player Name",
    lblMNum: "Shirt Number (#)",
    lblMPos: "Position",
    lblMPrice: "Value / Price Code (0-63)",
    lblMFit: "❤️ Fitness (0 - 100 %)",
    lblMStatus: "🚑 Player Status (Health / Ban)",
    lblMTeam: "🔄 Team (Transfer / Swap to another team)",
    lblMSkillsTitle: "Player Skills (0 - 7)",
    btnCancel: "Cancel",
    btnSave: "💾 Save changes to SWOS",
    // Positions in modal
    posOptGK: "GK (Goalkeeper)",
    posOptRB: "RB (Right Back)",
    posOptLB: "LB (Left Back)",
    posOptD: "D (Central Defender)",
    posOptRW: "RW (Right Wing)",
    posOptLW: "LW (Left Wing)",
    posOptM: "M (Midfielder)",
    posOptA: "A (Attacker)",
    // Toast & Alerts
    dbLoaded: (id) => `📁 Database loaded: ${id}`,
    transferSuccess: (name, team) => `✓ Transfer: ${name} -> ${team}`,
    transferError: (err) => `Transfer error: ${err}`,
    skillSaved: (name, skill, val) => `✓ ${name}: ${skill} = ${val}`,
    numSaved: (name, num) => `✓ ${name}: shirt #${num} saved`,
    fitnessSaved: (name, val) => `✓ ${name}: fitness set to ${val}%`,
    statusSaved: (name, label) => `✓ ${name}: status set to "${label}"`,
    saveSuccessTransferred: (name, team) => `✓ Transferred & Saved: ${name} -> ${team}`,
    saveSuccess: (name) => `✓ Saved: ${name}`,
    saveError: (err) => `Error saving: ${err}`,
    leagueFormat: (id, cnt, sample) => `League / Country: ${id} (${cnt} teams, e.g. ${sample})`,
    careerFormat: (id) => `💾 Saved Career: ${id}`,
    yourTeamBadge: " [YOUR TEAM]"
  }
};

function t(key, ...args) {
  const langTable = I18N[currentLang] || I18N.cs;
  const val = langTable[key] !== undefined ? langTable[key] : (I18N.cs[key] || key);
  if (typeof val === 'function') {
    return val(...args);
  }
  return val;
}

function getSkillsDef() {
  return [
    { key: 'passing', label: t('skillPassing'), shortLabel: t('colPassing'), idx: 0 },
    { key: 'shooting', label: t('skillShooting'), shortLabel: t('colShooting'), idx: 1 },
    { key: 'heading', label: t('skillHeading'), shortLabel: t('colHeading'), idx: 2 },
    { key: 'tackling', label: t('skillTackling'), shortLabel: t('colTackling'), idx: 3 },
    { key: 'control', label: t('skillControl'), shortLabel: t('colControl'), idx: 4 },
    { key: 'speed', label: t('skillSpeed'), shortLabel: t('colSpeed'), idx: 5 },
    { key: 'finishing', label: t('skillFinishing'), shortLabel: t('colFinishing'), idx: 6 }
  ];
}

function getTableColsDef() {
  return [
    { key: 'name', label: t('colName'), align: 'left' },
    { key: 'team_id', label: t('colTeam'), align: 'left', isTeam: true },
    { key: 'position', label: t('colPos') },
    { key: 'number', label: t('colNum'), isField: 'number' },
    { key: 'status_text', label: t('colStatus') },
    { key: 'fitness', label: t('colFitness'), isFitness: true },
    { key: 'overall', label: t('colOverall') },
    { key: 'passing', label: t('colPassing'), skillIdx: 0 },
    { key: 'shooting', label: t('colShooting'), skillIdx: 1 },
    { key: 'heading', label: t('colHeading'), skillIdx: 2 },
    { key: 'tackling', label: t('colTackling'), skillIdx: 3 },
    { key: 'control', label: t('colControl'), skillIdx: 4 },
    { key: 'speed', label: t('colSpeed'), skillIdx: 5 },
    { key: 'finishing', label: t('colFinishing'), skillIdx: 6 }
  ];
}

let SKILLS = getSkillsDef();
let TABLE_COLS = getTableColsDef();

function setLanguage(lang) {
  currentLang = (lang === 'en') ? 'en' : 'cs';
  try {
    localStorage.setItem('swos_lang', currentLang);
  } catch(e) {}

  document.getElementById('lang-btn-en').classList.toggle('active', currentLang === 'en');
  document.getElementById('lang-btn-cs').classList.toggle('active', currentLang === 'cs');

  // Static texts
  document.getElementById('app-title').innerHTML = `${t('appTitle')} <span class="unofficial-badge" id="lbl-unofficial">Unofficial</span>`;
  document.getElementById('lbl-db-select').textContent = t('lblDbSelect');
  document.getElementById('tableSearchInput').placeholder = t('searchPlaceholder');
  document.getElementById('tableHint').innerHTML = t('tableHint');

  // Filter dropdown positions
  const posSelect = document.getElementById('tablePosFilter');
  const prevPosVal = posSelect.value;
  posSelect.innerHTML = `
    <option value="">${t('allPositions')}</option>
    <option value="GK">${t('posGK')}</option>
    <option value="D">${t('posD')}</option>
    <option value="M">${t('posM')}</option>
    <option value="A">${t('posA')}</option>
  `;
  posSelect.value = prevPosVal;

  // First option of team filter
  const teamFilter = document.getElementById('tableTeamFilter');
  if (teamFilter && teamFilter.options.length > 0) {
    teamFilter.options[0].text = t('allTeams');
  }

  // Update DB Select options formatting
  const dbSelect = document.getElementById('dbSelect');
  if (dbSelect && allDatabases.length > 0) {
    allDatabases.forEach((db, idx) => {
      const opt = dbSelect.options[idx];
      if (opt) {
        if (db.type === 'career') {
          opt.textContent = t('careerFormat', db.id);
        } else {
          opt.textContent = t('leagueFormat', db.id, db.count, db.sample || '');
        }
      }
    });
  }

  // Update Modal static texts
  document.getElementById('lbl_mName').textContent = t('lblMName');
  document.getElementById('lbl_mNumber').textContent = t('lblMNum');
  document.getElementById('lbl_mPosition').textContent = t('lblMPos');
  document.getElementById('lbl_mPrice').textContent = t('lblMPrice');
  document.getElementById('lbl_mFitness').textContent = t('lblMFit');
  document.getElementById('lbl_mStatus').textContent = t('lblMStatus');
  document.getElementById('lbl_mTeam').textContent = t('lblMTeam');
  document.getElementById('lbl_mSkillsTitle').textContent = t('lblMSkillsTitle');
  document.getElementById('btnCancelModal').textContent = t('btnCancel');
  document.getElementById('btnSaveModal').textContent = t('btnSave');

  // Modal positions dropdown
  const mPos = document.getElementById('mPosition');
  const prevMPosVal = mPos.value;
  mPos.innerHTML = `
    <option value="GK">${t('posOptGK')}</option>
    <option value="RB">${t('posOptRB')}</option>
    <option value="LB">${t('posOptLB')}</option>
    <option value="D">${t('posOptD')}</option>
    <option value="RW">${t('posOptRW')}</option>
    <option value="LW">${t('posOptLW')}</option>
    <option value="M">${t('posOptM')}</option>
    <option value="A">${t('posOptA')}</option>
  `;
  mPos.value = prevMPosVal;

  // Modal status dropdown
  const mStatus = document.getElementById('mStatus');
  const prevMStatusVal = mStatus.value;
  mStatus.innerHTML = `
    <option value="0">${t('statusOk')}</option>
    <option value="32">${t('statusInjured1')}</option>
    <option value="64">${t('statusInjured2')}</option>
    <option value="4">${t('statusYellow')}</option>
    <option value="128">${t('statusBanned')}</option>
    <option value="196">${t('statusInjuredAndBanned')}</option>
  `;
  mStatus.value = prevMStatusVal;

  // Refresh table headers & definitions
  SKILLS = getSkillsDef();
  TABLE_COLS = getTableColsDef();

  // If modal open, refresh title and skills labels
  if (selectedPlayer) {
    document.getElementById("modalPlayerTitle").textContent = t('modalEditTitle', selectedPlayer.number, selectedPlayer.position, selectedPlayer.name, selectedPlayer.team_name);
    renderModalSkills();
  }

  // Update player counter & table
  if (allPlayers.length > 0) {
    document.getElementById("player-count").textContent = t('playerCount', allPlayers.length, currentDbId);
  }
  renderTable();
}

function showToast(msg) {
  const tEl = document.getElementById("toast");
  tEl.textContent = msg;
  tEl.classList.add("show");
  setTimeout(() => tEl.classList.remove("show"), 2500);
}

async function init() {
  const respDbs = await fetch("/api/databases");
  const dataDbs = await respDbs.json();
  allDatabases = dataDbs.databases;

  const dbSelect = document.getElementById("dbSelect");
  dbSelect.innerHTML = "";

  if (allDatabases.length > 0) {
    if (!allDatabases.some(db => db.id === currentDbId)) {
      currentDbId = allDatabases[0].id;
    }
  }

  allDatabases.forEach(db => {
    const opt = document.createElement("option");
    opt.value = db.id;
    if (db.type === 'career') {
      opt.textContent = t('careerFormat', db.id);
    } else {
      opt.textContent = t('leagueFormat', db.id, db.count, db.sample || '');
    }
    if (db.id === currentDbId) opt.selected = true;
    dbSelect.appendChild(opt);
  });

  setLanguage(currentLang);
  await loadDbData(currentDbId);
}

async function onDbChange() {
  currentDbId = document.getElementById("dbSelect").value;
  await loadDbData(currentDbId);
  showToast(t('dbLoaded', currentDbId));
}

async function loadDbData(dbId) {
  const resp = await fetch(`/api/players?db=${encodeURIComponent(dbId)}`);
  const data = await resp.json();
  allTeams = data.teams;
  allPlayers = data.players;

  const tFilter = document.getElementById("tableTeamFilter");
  tFilter.innerHTML = `<option value="">${t('allTeams')}</option>`;
  const mTeam = document.getElementById("mTeam");
  if (mTeam) mTeam.innerHTML = "";

  allTeams.forEach(tItem => {
    const opt = document.createElement("option");
    opt.value = tItem.id;
    // Format active team display name based on current language
    let teamDisplayName = tItem.name;
    if (tItem.is_active && tItem.raw_name) {
      const coachText = tItem.coach ? (currentLang === 'en' ? ` (manager: ${tItem.coach})` : ` (trenér: ${tItem.coach})`) : "";
      teamDisplayName = `⭐ ${tItem.raw_name}${coachText}${t('yourTeamBadge')}`;
    }
    opt.textContent = teamDisplayName;
    tFilter.appendChild(opt);
    if (mTeam) mTeam.appendChild(opt.cloneNode(true));
  });

  document.getElementById("player-count").textContent = t('playerCount', allPlayers.length, dbId);
  renderTable();
}

function onTableSort(colKey) {
  if (tableSortCol === colKey) {
    tableSortAsc = !tableSortAsc;
  } else {
    tableSortCol = colKey;
    tableSortAsc = (colKey === 'name' || colKey === 'team_id' || colKey === 'position');
  }
  renderTable();
}

function renderTable() {
  const q = document.getElementById("tableSearchInput").value.toLowerCase();
  const teamVal = document.getElementById("tableTeamFilter").value;
  const posVal = document.getElementById("tablePosFilter").value;

  let filtered = allPlayers.filter(p => {
    if (q && !p.name.toLowerCase().includes(q) && !p.team_name.toLowerCase().includes(q)) return false;
    if (teamVal !== "" && p.team_id != teamVal) return false;
    if (posVal === 'GK' && p.position !== 'GK') return false;
    if (posVal === 'D' && !['D', 'RB', 'LB'].includes(p.position)) return false;
    if (posVal === 'M' && !['M', 'RW', 'LW'].includes(p.position)) return false;
    if (posVal === 'A' && p.position !== 'A') return false;
    return true;
  });

  filtered.sort((a, b) => {
    let vA, vB;
    const colDef = TABLE_COLS.find(c => c.key === tableSortCol) || TABLE_COLS[0];

    if (colDef.skillIdx !== undefined) {
      vA = (a.skills && a.skills[colDef.skillIdx] !== undefined) ? a.skills[colDef.skillIdx] : -1;
      vB = (b.skills && b.skills[colDef.skillIdx] !== undefined) ? b.skills[colDef.skillIdx] : -1;
    } else if (tableSortCol === 'team_id') {
      vA = a.team_name || '';
      vB = b.team_name || '';
    } else {
      vA = a[tableSortCol];
      vB = b[tableSortCol];
    }

    if (typeof vA === 'string') {
      return tableSortAsc ? vA.localeCompare(vB) : vB.localeCompare(vA);
    } else {
      return tableSortAsc ? (vA - vB) : (vB - vA);
    }
  });

  document.getElementById("tableCount").textContent = t('tableCount', filtered.length);

  const theadTr = document.getElementById("tableHeaders");
  theadTr.innerHTML = "";
  TABLE_COLS.forEach(c => {
    const th = document.createElement("th");
    const isSorted = (tableSortCol === c.key);
    th.className = isSorted ? "active-sort" : "";
    th.onclick = () => onTableSort(c.key);
    th.innerHTML = `${c.label} <span class="sort-icon">${isSorted ? (tableSortAsc ? '▲' : '▼') : ''}</span>`;
    theadTr.appendChild(th);
  });

  const tbody = document.getElementById("tableBody");
  tbody.innerHTML = "";

  const teamOptionsHtml = allTeams.map(tItem => {
    let tName = tItem.name;
    if (tItem.is_active && tItem.raw_name) {
      const coachText = tItem.coach ? (currentLang === 'en' ? ` (manager: ${tItem.coach})` : ` (trenér: ${tItem.coach})`) : "";
      tName = `⭐ ${tItem.raw_name}${coachText}${t('yourTeamBadge')}`;
    }
    return `<option value="${tItem.id}">${tName}</option>`;
  }).join("");

  filtered.forEach(p => {
    const tr = document.createElement("tr");

    TABLE_COLS.forEach(c => {
      const td = document.createElement("td");

      if (c.key === 'name') {
        td.className = 'cell-name';
        td.title = t('nameTooltip');
        td.innerHTML = `✏️ ${p.name}`;
        td.onclick = () => openEditModal(p.id);
      } else if (c.isTeam) {
        td.className = 'cell-team';
        td.innerHTML = `<select class="inline-team" onchange="onInlineTeamChange(${p.id}, this)">${teamOptionsHtml}</select>`;
        const sel = td.querySelector("select");
        if (sel) sel.value = p.team_id;
      } else if (c.key === 'position') {
        td.innerHTML = `<span class="p-badge ${p.position}">${p.position}</span>`;
      } else if (c.key === 'number') {
        td.innerHTML = `<input type="text" class="inline-num" value="${p.number}" onfocus="this.select()" onchange="onInlineFieldChange(${p.id}, 'number', this)" onkeydown="if(event.key==='Enter') this.blur();">`;
      } else if (c.key === 'status_text') {
        const sc = p.status_code || 0;
        td.innerHTML = `
          <select class="inline-select-status ${p.status_type}" onchange="onInlineStatusChange(${p.id}, this)">
            <option value="0" ${sc === 0 ? 'selected' : ''}>${t('statusOk')}</option>
            <option value="32" ${(sc & 0x20) ? 'selected' : ''}>${t('statusInjured1')}</option>
            <option value="4" ${(sc & 0x04) ? 'selected' : ''}>${t('statusYellow')}</option>
            <option value="128" ${(sc & 0x80) ? 'selected' : ''}>${t('statusBanned')}</option>
          </select>
        `;
      } else if (c.key === 'fitness') {
        const fit = p.fitness !== undefined ? p.fitness : 100;
        const colorCls = fit >= 80 ? 'high' : (fit >= 60 ? 'mid' : 'low');
        td.innerHTML = `
          <div class="fit-wrap" title="${t('fitTooltip')}">
            <input type="number" min="0" max="100" class="inline-num inline-fit" value="${fit}" onfocus="this.select()" onchange="onInlineFitnessChange(${p.id}, this)" onkeydown="if(event.key==='Enter') this.blur();">%
            <div class="fit-bar-bg"><div class="fit-bar-fill ${colorCls}" style="width: ${fit}%;"></div></div>
          </div>
        `;
      } else if (c.key === 'overall') {
        const val = p.overall || 0;
        td.innerHTML = `<b class="${val >= 28 ? 'val-high' : (val >= 14 ? 'val-mid' : 'val-low')}">${p.is_gk ? 'GK' : val}</b>`;
      } else if (c.skillIdx !== undefined) {
        if (p.is_gk) {
          td.innerHTML = `<span style="color:#64748b;">-</span>`;
        } else {
          const val = p.skills[c.skillIdx] || 0;
          const colorCls = val >= 6 ? 'val-high' : (val >= 4 ? 'val-mid' : (val <= 1 ? 'val-low' : ''));
          td.innerHTML = `<input type="number" min="0" max="7" class="inline-num ${colorCls}" value="${val}" onfocus="this.select()" onchange="onInlineSkillChange(${p.id}, ${c.skillIdx}, this)" onkeydown="if(event.key==='Enter') this.blur();">`;
        }
      } else {
        td.textContent = p[c.key] || '';
      }
      tbody.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

async function onInlineTeamChange(playerId, selectEl) {
  const newTeamId = parseInt(selectEl.value);
  const player = allPlayers.find(p => p.id === playerId);
  if (!player) return;

  if (player.team_id === newTeamId) return;

  const targetTeam = allTeams.find(tItem => tItem.id === newTeamId);
  const targetTeamName = targetTeam ? targetTeam.name : `Tým ${newTeamId}`;

  const payload = {
    db_id: currentDbId,
    file_offset: player.file_offset,
    target_team_id: newTeamId
  };

  const resp = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const res = await resp.json();
  if (res.ok) {
    showToast(t('transferSuccess', player.name, targetTeamName));
    await loadDbData(currentDbId);
  } else {
    alert(t('transferError', res.error));
    selectEl.value = player.team_id;
  }
}

async function onInlineSkillChange(playerId, skillIdx, inputEl) {
  let val = parseInt(inputEl.value);
  if (isNaN(val)) val = 0;
  val = Math.max(0, Math.min(7, val));
  inputEl.value = val;

  const player = allPlayers.find(p => p.id === playerId);
  if (!player) return;

  player.skills[skillIdx] = val;
  player.overall = player.skills.reduce((a, b) => a + b, 0);

  updateInputColor(inputEl, val);

  const payload = {
    db_id: currentDbId,
    file_offset: player.file_offset,
    skills: player.skills
  };

  const resp = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const res = await resp.json();
  if (res.ok) {
    inputEl.classList.add('saved-flash');
    setTimeout(() => inputEl.classList.remove('saved-flash'), 800);
    showToast(t('skillSaved', player.name, SKILLS[skillIdx].label, val));
  }
}

async function onInlineFieldChange(playerId, fieldName, inputEl) {
  let val = parseInt(inputEl.value);
  if (isNaN(val)) val = 1;
  val = Math.max(1, Math.min(99, val));
  inputEl.value = val;

  const player = allPlayers.find(p => p.id === playerId);
  if (!player) return;

  player.number = val;

  const payload = {
    db_id: currentDbId,
    file_offset: player.file_offset,
    number: player.number
  };

  const resp = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const res = await resp.json();
  if (res.ok) {
    inputEl.classList.add('saved-flash');
    setTimeout(() => inputEl.classList.remove('saved-flash'), 800);
    showToast(t('numSaved', player.name, val));
  }
}

async function onInlineFitnessChange(playerId, inputEl) {
  let val = parseInt(inputEl.value);
  if (isNaN(val)) val = 100;
  val = Math.max(0, Math.min(100, val));
  inputEl.value = val;

  const player = allPlayers.find(p => p.id === playerId);
  if (!player) return;

  player.fitness = val;

  const barFill = inputEl.parentElement.querySelector('.fit-bar-fill');
  if (barFill) {
    barFill.style.width = val + '%';
    barFill.className = 'fit-bar-fill ' + (val >= 80 ? 'high' : (val >= 60 ? 'mid' : 'low'));
  }

  const payload = {
    db_id: currentDbId,
    file_offset: player.file_offset,
    fitness: val
  };

  const resp = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const res = await resp.json();
  if (res.ok) {
    inputEl.classList.add('saved-flash');
    setTimeout(() => inputEl.classList.remove('saved-flash'), 800);
    showToast(t('fitnessSaved', player.name, val));
  }
}

async function onInlineStatusChange(playerId, selectEl) {
  const statusCode = parseInt(selectEl.value) || 0;
  const player = allPlayers.find(p => p.id === playerId);
  if (!player) return;

  player.status_code = statusCode;

  selectEl.className = 'inline-select-status ' + (
    (statusCode & 0x20 || statusCode & 0x40) ? 'injured' : (
      (statusCode & 0x80) ? 'banned' : (
        (statusCode & 0x04) ? 'warning' : 'ok'
      )
    )
  );

  const payload = {
    db_id: currentDbId,
    file_offset: player.file_offset,
    status_code: statusCode
  };

  const resp = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const res = await resp.json();
  if (res.ok) {
    selectEl.classList.add('saved-flash');
    setTimeout(() => selectEl.classList.remove('saved-flash'), 800);
    const label = selectEl.options[selectEl.selectedIndex].text;
    showToast(t('statusSaved', player.name, label));
  }
}

function updateInputColor(el, val) {
  el.classList.remove('val-high', 'val-mid', 'val-low');
  if (val >= 6) el.classList.add('val-high');
  else if (val >= 4) el.classList.add('val-mid');
  else if (val <= 1) el.classList.add('val-low');
}

function openEditModal(playerId) {
  selectedPlayer = allPlayers.find(p => p.id === playerId);
  if (!selectedPlayer) return;

  document.getElementById("modalPlayerTitle").textContent = t('modalEditTitle', selectedPlayer.number, selectedPlayer.position, selectedPlayer.name, selectedPlayer.team_name);
  document.getElementById("mName").value = selectedPlayer.name;
  document.getElementById("mNumber").value = selectedPlayer.number;
  document.getElementById("mPosition").value = selectedPlayer.position;
  document.getElementById("mPrice").value = selectedPlayer.price_code;

  const mTeam = document.getElementById("mTeam");
  if (mTeam) mTeam.value = selectedPlayer.team_id;

  const fit = (selectedPlayer.fitness !== undefined) ? selectedPlayer.fitness : 100;
  document.getElementById("mFitness").value = fit;
  document.getElementById("mFitnessVal").textContent = fit + '%';

  const statusCode = selectedPlayer.status_code || 0;
  const statusSelect = document.getElementById("mStatus");
  let matched = false;
  for (let opt of statusSelect.options) {
    if (parseInt(opt.value) === statusCode) {
      statusSelect.value = opt.value;
      matched = true;
      break;
    }
  }
  if (!matched) statusSelect.value = "0";

  renderModalSkills();
  document.getElementById("editModal").classList.add("show");
}

function closeModal() {
  document.getElementById("editModal").classList.remove("show");
}

function onPositionChange() {
  if (!selectedPlayer) return;
  selectedPlayer.position = document.getElementById("mPosition").value;
  selectedPlayer.is_gk = (selectedPlayer.position === 'GK');
  renderModalSkills();
}

function renderModalSkills() {
  const container = document.getElementById("modalAttrContainer");
  const section = document.getElementById("modalSkillsSection");
  if (selectedPlayer.is_gk) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";
  container.innerHTML = "";

  SKILLS.forEach(s => {
    const val = Math.max(0, Math.min(7, selectedPlayer.skills[s.idx] || 0));
    const div = document.createElement("div");
    div.className = "form-group";
    div.innerHTML = `
      <label>${s.label}</label>
      <div class="attr-slider-wrap">
        <input type="range" min="0" max="7" value="${val}" id="m_skill_${s.idx}" oninput="document.getElementById('m_val_${s.idx}').textContent = this.value">
        <span class="attr-val" id="m_val_${s.idx}">${val}</span>
      </div>
    `;
    container.appendChild(div);
  });
}

async function saveModalPlayer() {
  if (!selectedPlayer) return;

  const newSkills = [];
  if (!selectedPlayer.is_gk) {
    for (let i = 0; i < 7; i++) {
      const input = document.getElementById(`m_skill_${i}`);
      let v = input ? parseInt(input.value) : 0;
      if (isNaN(v)) v = 0;
      v = Math.max(0, Math.min(7, v));
      newSkills.push(v);
    }
  } else {
    newSkills.push(0, 0, 0, 0, 0, 0, 0);
  }

  const newFit = parseInt(document.getElementById("mFitness").value) || 100;
  const newStatusCode = parseInt(document.getElementById("mStatus").value) || 0;
  const mTeam = document.getElementById("mTeam");
  const targetTeamId = mTeam ? parseInt(mTeam.value) : selectedPlayer.team_id;

  const payload = {
    db_id: currentDbId,
    file_offset: selectedPlayer.file_offset,
    name: document.getElementById("mName").value,
    number: parseInt(document.getElementById("mNumber").value) || 1,
    position: document.getElementById("mPosition").value,
    price_code: parseInt(document.getElementById("mPrice").value) || 0,
    skills: newSkills,
    fitness: newFit,
    status_code: newStatusCode,
    target_team_id: targetTeamId
  };

  const resp = await fetch("/api/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const res = await resp.json();
  if (res.ok) {
    closeModal();
    const isTransferred = (targetTeamId !== selectedPlayer.team_id);
    const targetTeam = allTeams.find(tItem => tItem.id === targetTeamId);
    const targetTeamName = targetTeam ? targetTeam.name : `Tým ${targetTeamId}`;
    if (isTransferred) {
      showToast(t('saveSuccessTransferred', payload.name, targetTeamName));
    } else {
      showToast(t('saveSuccess', payload.name));
    }
    await loadDbData(currentDbId);
  } else {
    alert(t('saveError', res.error));
  }
}

init();
</script>
</body>
</html>
"""


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler providing API endpoints and web UI for SWOS editor."""

    def do_GET(self):  # pylint: disable=invalid-name
        """Handles HTTP GET requests for HTML page and API reads."""
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif parsed.path == "/api/databases":
            try:
                dbs = list_databases()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"databases": dbs}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path == "/api/players":
            db_id = qs.get("db", ["TEAM.008"])[0]
            try:
                teams, players = load_players_from_db(db_id)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"teams": teams, "players": players, "db_id": db_id}
                    ).encode("utf-8")
                )
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # pylint: disable=invalid-name
        """Handles HTTP POST requests for saving player edits."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/save":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            try:
                data = json.loads(body.decode("utf-8"))
                db_id = data.get("db_id", "TEAM.008")
                file_offset = int(data["file_offset"])
                save_player(db_id, file_offset, data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        """Suppresses default HTTP server logging to keep terminal output clean."""


def main():
    """Main CLI entrypoint for launching SWOS Editor server."""
    parser = argparse.ArgumentParser(description="SWOS 96/97 Save & Player Editor")
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Path to SWOS directory (default: script directory)",
    )
    parser.add_argument(
        "--port", type=int, default=8096, help="Port for web interface (default: 8096)"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not automatically open browser"
    )
    args = parser.parse_args()

    if args.dir:
        set_base_dir(args.dir)

    port = args.port
    server = HTTPServer(("127.0.0.1", port), RequestHandler)
    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print(f"⚽ SWOS 96/97 Editor running at: {url}")
    print(f"   Game directory: {BASE_DIR}")
    print("   Press Ctrl+C in terminal to stop.")
    print("=" * 60)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()

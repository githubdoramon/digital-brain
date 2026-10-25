"""Provider-observed profiles and verified aliases, independent of message keys."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from messaging_links import phone, username
from messaging_normalization import Provider, jid, stable_id
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def telegram_profile(entity):
    from telethon.tl.types import User

    if not isinstance(entity, User) or entity.min or not entity.id:
        return None  # Partial group entities cannot erase a known profile.
    from telegram_normalization import display_name

    return {
        "external_id": f"{entity.id}@telegram.user",
        "display_name": display_name(entity),
        "username": username(entity.username),
        "phone": phone(entity.phone),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_profiles(profiles):
    if not isinstance(profiles, list) or len(profiles) > 2:
        raise ValueError("invalid_profiles")
    result = []
    for profile in profiles:
        external = jid(profile["external_id"])
        if not external.endswith('@telegram.user'):
            raise ValueError('invalid_profile_identity')
        observed = datetime.fromisoformat(profile['observed_at'])
        if observed.tzinfo is None or observed > datetime.now(timezone.utc):
            raise ValueError('invalid_profile_timestamp')
        result.append({
            'external_id': external, 'display_name': str(profile.get('display_name', ''))[:512],
            'username': username(profile.get('username')), 'phone': phone(profile.get('phone')),
            'observed_at': observed.isoformat(),
        })
    return result


def verified_pair(first, second):
    try:
        values = [jid(first), jid(second)]
    except (ValueError, AttributeError, TypeError):
        return None
    lid = next((v for v in values if re.fullmatch(r'[1-9][0-9]*@lid', v)), None)
    pn = next((v for v in values if re.fullmatch(r'[1-9][0-9]{6,14}@s[.]whatsapp[.]net', v)), None)
    return {'lid': lid, 'phone': pn} if lid and pn else None


def validate_mappings(mappings):
    if not isinstance(mappings, list) or len(mappings) > 4:
        raise ValueError('invalid_mappings')
    result = []
    for mapping in mappings:
        pair = verified_pair(mapping.get('lid'), mapping.get('phone'))
        if not pair:
            raise ValueError('invalid_mapping')
        if pair not in result:
            result.append(pair)
    return result


def eligible(cur, account_id, generation, provider):
    cur.execute('''SELECT self_jid FROM messaging_accounts WHERE account_id=%s
        AND generation=%s AND enabled AND provider=%s FOR UPDATE''',
                (account_id, generation, provider.value))
    return cur.fetchone()


def retain_profile(cur, account_id, profile, self_jid=''):
    cur.execute('''INSERT INTO messaging_identities
        (identity_id, account_id, external_id, display_name, is_self,
         source_username, source_phone, profile_observed_at, profile_next_refresh_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW()+INTERVAL '24 hours')
        ON CONFLICT(identity_id) DO UPDATE SET
          display_name=COALESCE(NULLIF(EXCLUDED.display_name,''), messaging_identities.display_name),
          source_username=EXCLUDED.source_username, source_phone=EXCLUDED.source_phone,
          profile_observed_at=EXCLUDED.profile_observed_at,
          profile_next_refresh_at=EXCLUDED.profile_next_refresh_at
        WHERE messaging_identities.profile_observed_at IS NULL
           OR EXCLUDED.profile_observed_at >= messaging_identities.profile_observed_at''',
                (stable_id(account_id, profile['external_id']), account_id, profile['external_id'],
                 profile['display_name'], profile['external_id'] == self_jid,
                 profile['username'], profile['phone'], profile['observed_at']))


def retain_mapping(cur, account_id, mapping):
    # Callers hold the account lock, shared with manual contact-link changes.
    lid, pn = mapping['lid'], mapping['phone']
    cur.execute('''SELECT 1 FROM messaging_identity_mappings WHERE account_id=%s
        AND (lid=%s OR phone=%s) AND (lid<>%s OR phone<>%s) LIMIT 1''',
                (account_id, lid, pn, lid, pn))
    conflict = bool(cur.fetchone())
    if conflict:
        cur.execute('''UPDATE messaging_identity_mappings SET conflicted=TRUE
            WHERE account_id=%s AND (lid=%s OR phone=%s)''', (account_id, lid, pn))
    cur.execute('''INSERT INTO messaging_identity_mappings(account_id,lid,phone,conflicted)
        VALUES (%s,%s,%s,%s) ON CONFLICT(account_id,lid,phone) DO UPDATE
        SET verified_at=NOW(), conflicted=messaging_identity_mappings.conflicted OR EXCLUDED.conflicted
        RETURNING conflicted''', (account_id, lid, pn, conflict))
    if cur.fetchone()['conflicted']:
        return
    cur.execute('''SELECT contact_id,is_self FROM messaging_identities
        WHERE account_id=%s AND external_id IN (%s,%s)''', (account_id, lid, pn))
    existing = cur.fetchall()
    for external in (lid, pn):
        cur.execute('''INSERT INTO messaging_identities(identity_id,account_id,external_id)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''', (stable_id(account_id, external), account_id, external))
    if any(row['is_self'] for row in existing):
        cur.execute('UPDATE messaging_identities SET is_self=TRUE WHERE account_id=%s AND external_id IN (%s,%s)', (account_id, lid, pn))
    contacts = {row['contact_id'] for row in existing if row['contact_id']}
    if len(contacts) == 1:
        target = contacts.pop()
        cur.execute('''UPDATE messaging_identities SET contact_id=%s,link_origin='identifier'
            WHERE account_id=%s AND external_id IN (%s,%s) AND contact_id IS NULL
            RETURNING identity_id''', (target, account_id, lid, pn))
        for row in cur.fetchall():
            cur.execute('''INSERT INTO messaging_identity_links(identity_id,old_contact_id,new_contact_id)
                VALUES (%s,NULL,%s)''', (row['identity_id'], target))


def observe_mappings(account_id, generation, mappings):
    import messaging_archive as archive

    mappings = validate_mappings(mappings)
    with archive.get_conn() as conn, conn.cursor() as cur:
        if not eligible(cur, account_id, generation, Provider.WHATSAPP):
            return
        for mapping in mappings:
            retain_mapping(cur, account_id, mapping)
        conn.commit()


def claim_refresh(account_id, generation, provider, limit=5):
    import messaging_archive as archive

    suffix = '%@telegram.user' if provider == Provider.TELEGRAM else '%@s.whatsapp.net'
    with archive.get_conn() as conn, conn.cursor() as cur:
        if not eligible(cur, account_id, generation, provider):
            return []
        cur.execute('''SELECT identity_id, external_id FROM messaging_identities
            WHERE account_id=%s AND external_id LIKE %s
              AND (profile_next_refresh_at IS NULL OR profile_next_refresh_at<=NOW())
            ORDER BY profile_next_refresh_at NULLS FIRST,identity_id LIMIT %s FOR UPDATE''',
                    (account_id, suffix, limit))
        rows = cur.fetchall()
        for row in rows:
            # Failed/unresolvable people back off, allowing the rest of history a turn.
            cur.execute("UPDATE messaging_identities SET profile_next_refresh_at=NOW()+INTERVAL '1 hour' WHERE identity_id=%s",
                        (row['identity_id'],))
        conn.commit()
        return rows


async def refresh_telegram_profiles(client, account_id, generation):
    from telethon.errors import FloodWaitError
    from telethon.tl.types import PeerUser

    import messaging_archive as archive

    rows = await asyncio.to_thread(claim_refresh, account_id, generation, Provider.TELEGRAM)
    updated = 0
    for row in rows:
        try:
            entity = await asyncio.wait_for(client.get_entity(PeerUser(int(row['external_id'].split('@')[0]))), 10)
            profile = telegram_profile(entity)
            if not profile or profile['external_id'] != row['external_id']:
                continue
            def save(profile=profile):
                with archive.get_conn() as conn, conn.cursor() as cur:
                    account = eligible(cur, account_id, generation, Provider.TELEGRAM)
                    if account:
                        retain_profile(cur, account_id, profile, account['self_jid'])
                        conn.commit()
                        return True
                    return False
            updated += int(await asyncio.to_thread(save))
        except FloodWaitError:
            raise
        except (ValueError, TimeoutError):
            continue
    if rows:
        logger.info('[messaging_identity] Telegram profile refresh attempted=%s updated=%s', len(rows), updated)


async def refresh_whatsapp_mappings(client, account_id, generation):
    from tryx.types import JID

    rows = await asyncio.to_thread(claim_refresh, account_id, generation, Provider.WHATSAPP)
    verified = 0
    for row in rows:
        try:
            # Provider lookup is keyed by the exact archived phone JID. Never zip
            # an unordered response with requested numbers or match display names.
            result = await asyncio.wait_for(client.contact.get_user_info(JID(row['external_id'].split('@')[0], 's.whatsapp.net')), 10)
            pairs = []
            for key, info in result.items():
                if jid(key) == row['external_id']:
                    pair = verified_pair(key, info.lid)
                    if pair:
                        pairs.append(pair)
            await asyncio.to_thread(observe_mappings, account_id, generation, pairs)
            if pairs:
                verified += len(pairs)
                await asyncio.to_thread(mark_refreshed, account_id, generation, row['identity_id'])
        except (ValueError, RuntimeError, TimeoutError):
            continue
    if rows:
        logger.info('[messaging_identity] WhatsApp mapping refresh attempted=%s verified=%s', len(rows), verified)


def mark_refreshed(account_id, generation, identity_id):
    import messaging_archive as archive

    with archive.get_conn() as conn, conn.cursor() as cur:
        if eligible(cur, account_id, generation, Provider.WHATSAPP):
            cur.execute("UPDATE messaging_identities SET profile_next_refresh_at=NOW()+INTERVAL '24 hours' WHERE identity_id=%s AND account_id=%s",
                        (identity_id, account_id))
            conn.commit()

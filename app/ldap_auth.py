from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.auth_config import AuthSettings


@dataclass(frozen=True)
class LdapIdentity:
    username: str
    display_name: str
    email: str
    groups: tuple[str, ...]
    permissions: frozenset[str]


class LdapAuthenticationError(Exception):
    pass


def _escape_filter_chars(value: str) -> str:
    # RFC 4515 filter escaping. Keeping this tiny helper local also lets the
    # rest of the application start before the optional LDAP client is used.
    return (
        value.replace("\\", r"\5c")
        .replace("*", r"\2a")
        .replace("(", r"\28")
        .replace(")", r"\29")
        .replace("\x00", r"\00")
    )


def _server_url_parts(url: str) -> tuple[str, int, bool]:
    parsed = urlparse(url)
    if parsed.scheme not in {"ldap", "ldaps"} or not parsed.hostname:
        raise LdapAuthenticationError("Invalid LDAP URL")
    use_ssl = parsed.scheme == "ldaps"
    port = parsed.port or (636 if use_ssl else 389)
    return parsed.hostname, port, use_ssl


def _absolute_dn(value: str, base_dn: str) -> str:
    value = value.strip().strip(",")
    base_dn = base_dn.strip().strip(",")
    if not value:
        return base_dn
    if value.lower().endswith(base_dn.lower()):
        return value
    return f"{value},{base_dn}"


def _unescape_dn_value(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        token = match.group(1)
        if len(token) == 2 and all(ch in "0123456789abcdefABCDEF" for ch in token):
            try:
                return bytes.fromhex(token).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return "\\" + token
        return token

    return re.sub(r"\\([0-9A-Fa-f]{2}|.)", repl, value)


def _cn_from_dn(dn: str) -> str | None:
    match = re.search(r"(?:^|,)\s*CN=((?:\\.|[^,])*)", dn, flags=re.IGNORECASE)
    if not match:
        return None
    return _unescape_dn_value(match.group(1).strip())


def _group_names(member_of_values: list[str]) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for dn in member_of_values:
        text = str(dn).strip()
        candidates = [text]
        cn = _cn_from_dn(text)
        if cn:
            candidates.append(cn)
        for candidate in candidates:
            normalized = candidate.casefold()
            if normalized not in seen:
                seen.add(normalized)
                names.append(candidate)
    return tuple(names)


def _in_group(groups: tuple[str, ...], configured: str) -> bool:
    target = configured.strip().casefold()
    if not target:
        return False
    return any(group.casefold() == target for group in groups)



def _permissions_for_groups(settings: AuthSettings, groups: tuple[str, ...]) -> frozenset[str] | None:
    is_admin = _in_group(groups, settings.ldap_admin_group)
    is_editor = _in_group(groups, settings.ldap_editor_group)
    has_access_group = _in_group(groups, settings.ldap_access_group)
    access_configured = bool(settings.ldap_access_group.strip())
    if access_configured and not (has_access_group or is_editor or is_admin):
        return None
    permissions: set[str] = set()
    if is_editor or is_admin:
        permissions.add("edit")
    if is_admin:
        permissions.add("admin")
    return frozenset(permissions)

def _entry_values(entry, attribute: str) -> list[str]:
    if not attribute:
        return []
    try:
        value = entry[attribute]
        values = value.values
    except (KeyError, AttributeError):
        return []
    return [str(item) for item in values if item is not None]


def _entry_first(entry, attribute: str, default: str = "") -> str:
    values = _entry_values(entry, attribute)
    return values[0] if values else default


def _make_server(settings: AuthSettings):
    try:
        from ldap3 import NONE, Server, Tls
    except ImportError as exc:  # pragma: no cover - production dependency check
        raise LdapAuthenticationError("ldap3 is not installed") from exc

    host, port, use_ssl = _server_url_parts(settings.ldap_url)
    tls = None
    if use_ssl:
        ca_file = settings.ldap_ca_cert_file or None
        if ca_file and not Path(ca_file).is_file():
            raise LdapAuthenticationError("LDAP CA certificate file does not exist")
        # Validate LDAPS certificates. With no explicit CA file ldap3/Python
        # uses the platform trust configuration; internal PKI can be mounted
        # and referenced with ldap_ca_cert_file.
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=ca_file)
    return Server(
        host,
        port=port,
        use_ssl=use_ssl,
        tls=tls,
        get_info=NONE,
        connect_timeout=10,
    )


def authenticate_ldap(settings: AuthSettings, username: str, password: str) -> LdapIdentity | None:
    """Authenticate an LDAP/AD user and map direct group membership to MediaDrop roles.

    A service/anonymous connection searches for the user DN, then a second bind
    validates the submitted password. Role groups are read from the configured
    memberOf-style attribute. LDAP failures deliberately return None so local
    legacy authentication can be attempted afterwards.
    """
    if not settings.ldap_enabled or not username or not password:
        return None
    try:
        from ldap3 import AUTO_BIND_NO_TLS, Connection

        server = _make_server(settings)
        search_connection = Connection(
            server,
            user=settings.ldap_bind_dn or None,
            password=settings.ldap_bind_password or None,
            auto_bind=AUTO_BIND_NO_TLS,
            receive_timeout=15,
            raise_exceptions=True,
        )
        escaped = _escape_filter_chars(username.strip())
        identity_parts = [f"({settings.ldap_username_attribute}={escaped})"]
        if settings.ldap_email_attribute:
            identity_parts.append(f"({settings.ldap_email_attribute}={escaped})")
        identity_filter = identity_parts[0]
        if len(identity_parts) > 1:
            identity_filter = "(|" + "".join(identity_parts) + ")"
        fixed_filter = settings.ldap_user_filter.strip() or "(objectClass=*)"
        search_filter = f"(&{fixed_filter}{identity_filter})"
        search_base = _absolute_dn(settings.ldap_user_base_dn, settings.ldap_base_dn)
        attributes = list(
            dict.fromkeys(
                attr
                for attr in (
                    settings.ldap_username_attribute,
                    settings.ldap_display_name_attribute,
                    settings.ldap_email_attribute,
                    settings.ldap_member_of_attribute,
                )
                if attr
            )
        )
        search_connection.search(
            search_base=search_base,
            search_filter=search_filter,
            attributes=attributes,
            size_limit=2,
        )
        entries = list(search_connection.entries)
        search_connection.unbind()
        if len(entries) != 1:
            return None
        entry = entries[0]
        user_dn = entry.entry_dn

        user_connection = Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=AUTO_BIND_NO_TLS,
            receive_timeout=15,
            raise_exceptions=True,
        )
        user_connection.unbind()

        groups = _group_names(_entry_values(entry, settings.ldap_member_of_attribute))
        # Editor/admin role membership also implies site access. This avoids
        # forcing administrators into two AD groups while still allowing a
        # separate broad access group for viewers.
        permissions = _permissions_for_groups(settings, groups)
        if permissions is None:
            return None

        canonical_username = _entry_first(entry, settings.ldap_username_attribute, username)
        display_name = _entry_first(entry, settings.ldap_display_name_attribute, canonical_username)
        email = _entry_first(entry, settings.ldap_email_attribute, "")
        return LdapIdentity(
            username=canonical_username,
            display_name=display_name,
            email=email,
            groups=groups,
            permissions=permissions,
        )
    except Exception:
        # Invalid password, unavailable directory, TLS failure and LDAP search
        # errors are intentionally indistinguishable here. The caller will try
        # the local legacy account next and ultimately return one generic login error.
        return None

"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

type NavUser = {
  name: string | null;
  image: string | null;
};

type NavItem = {
  href: string;
  label: string;
};

type NavBarClientProps = {
  user: NavUser | null;
};

const primaryItems: NavItem[] = [
  { href: "/", label: "Home" },
  { href: "/contacts", label: "Contacts" },
  { href: "/documents", label: "Documents" },
  { href: "/todos", label: "Todos" },
];

const secondaryItems: NavItem[] = [
  { href: "/meetings", label: "Meetings" },
  { href: "/system", label: "System Status" },
  { href: "/tools", label: "Tools" },
  { href: "/robots", label: "Robots" },
];

function isActivePath(pathname: string, href: string) {
  if (href === "/") {
    return pathname === "/";
  }

  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLink({ href, label, pathname, onNavigate }: NavItem & { pathname: string; onNavigate?: () => void }) {
  const active = isActivePath(pathname, href);

  return (
    <Link
      href={href}
      onClick={onNavigate}
      style={{
        color: active ? "#10233d" : "#4a5668",
        background: active ? "rgba(16, 35, 61, 0.08)" : "transparent",
        padding: "10px 14px",
        borderRadius: "999px",
        textDecoration: "none",
        fontSize: "0.95rem",
        fontWeight: active ? 600 : 500,
        lineHeight: 1,
        whiteSpace: "nowrap",
        transition: "background-color 160ms ease, color 160ms ease",
      }}
    >
      {label}
    </Link>
  );
}

export function NavBarClient({ user }: NavBarClientProps) {
  const pathname = usePathname();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  const moreMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setMobileMenuOpen(false);
    setMoreMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!moreMenuOpen) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      if (!moreMenuRef.current?.contains(event.target as Node)) {
        setMoreMenuOpen(false);
      }
    }

    function handleEscapeKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMoreMenuOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscapeKey);

    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscapeKey);
    };
  }, [moreMenuOpen]);

  const moreSectionActive = useMemo(
    () => secondaryItems.some((item) => isActivePath(pathname, item.href)),
    [pathname]
  );

  return (
    <header className="nav-shell">
      <div className="nav-inner">
        <div className="nav-top-row">
          <Link href="/" className="brand-link" aria-label="Digital Brain home">
            <span>Digital</span>
            <span>Brain</span>
          </Link>

          <div className="desktop-nav-group">
            <nav aria-label="Primary" className="desktop-nav">
              {primaryItems.map((item) => (
                <NavLink key={item.href} {...item} pathname={pathname} />
              ))}

              <div className="more-menu-wrap" ref={moreMenuRef}>
                <button
                  type="button"
                  className={`more-menu-trigger${moreMenuOpen ? " is-open" : ""}${moreSectionActive ? " is-active" : ""}`}
                  onClick={() => setMoreMenuOpen((current) => !current)}
                  aria-expanded={moreMenuOpen}
                  aria-haspopup="menu"
                >
                  More
                  <span className="menu-caret">+</span>
                </button>

                {moreMenuOpen ? (
                  <div className="more-menu-panel" role="menu" aria-label="More navigation">
                    {secondaryItems.map((item) => {
                      const active = isActivePath(pathname, item.href);

                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          role="menuitem"
                          className={`menu-link${active ? " is-active" : ""}`}
                          onClick={() => setMoreMenuOpen(false)}
                        >
                          {item.label}
                        </Link>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </nav>

            {user ? (
              <div className="desktop-account">
                <div className="user-chip">
                  {user.image ? (
                    <Image
                      src={user.image}
                      alt={user.name || "User"}
                      width={36}
                      height={36}
                      className="user-avatar"
                      unoptimized
                    />
                  ) : (
                    <div className="user-avatar user-avatar-fallback" aria-hidden="true">
                      {(user.name || "U").charAt(0).toUpperCase()}
                    </div>
                  )}
                  <span className="user-name">{user.name || "Signed in"}</span>
                </div>

                <Link href="/api/auth/signout" className="sign-out-link">
                  Sign Out
                </Link>
              </div>
            ) : null}
          </div>

          <button
            type="button"
            className={`mobile-menu-button${mobileMenuOpen ? " is-open" : ""}`}
            onClick={() => setMobileMenuOpen((current) => !current)}
            aria-expanded={mobileMenuOpen}
            aria-controls="mobile-site-menu"
          >
            Menu
          </button>
        </div>

        {mobileMenuOpen ? (
          <div id="mobile-site-menu" className="mobile-menu-panel">
            <nav aria-label="Mobile primary" className="mobile-nav-grid">
              {[...primaryItems, ...secondaryItems].map((item) => (
                <NavLink key={item.href} {...item} pathname={pathname} onNavigate={() => setMobileMenuOpen(false)} />
              ))}
            </nav>

            {user ? (
              <div className="mobile-account-row">
                <div className="user-chip mobile-user-chip">
                  {user.image ? (
                    <Image
                      src={user.image}
                      alt={user.name || "User"}
                      width={40}
                      height={40}
                      className="user-avatar"
                      unoptimized
                    />
                  ) : (
                    <div className="user-avatar user-avatar-fallback" aria-hidden="true">
                      {(user.name || "U").charAt(0).toUpperCase()}
                    </div>
                  )}
                  <span className="user-name">{user.name || "Signed in"}</span>
                </div>

                <Link href="/api/auth/signout" className="sign-out-link mobile-sign-out-link">
                  Sign Out
                </Link>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <style jsx>{`
        .nav-shell {
          position: sticky;
          top: 0;
          z-index: 40;
          border-bottom: 1px solid rgba(142, 152, 166, 0.28);
          background:
            linear-gradient(180deg, rgba(250, 251, 252, 0.97), rgba(245, 247, 250, 0.94)),
            #f8fafc;
          backdrop-filter: blur(14px);
        }

        .nav-inner {
          margin: 0 auto;
          max-width: 1080px;
          padding: 14px 24px;
        }

        .nav-top-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
        }

        .brand-link {
          display: inline-flex;
          flex-direction: column;
          gap: 0;
          color: #111827;
          font-size: 1.35rem;
          font-weight: 700;
          line-height: 0.95;
          letter-spacing: -0.04em;
          text-decoration: none;
          min-width: fit-content;
        }

        .desktop-nav-group {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 16px;
          flex: 1;
          min-width: 0;
        }

        .desktop-nav {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 6px;
          flex-wrap: wrap;
        }

        .more-menu-wrap {
          position: relative;
        }

        .more-menu-trigger {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border: 0;
          background: transparent;
          color: #4a5668;
          padding: 10px 14px;
          border-radius: 999px;
          font-size: 0.95rem;
          font-weight: 500;
          cursor: pointer;
          transition: background-color 160ms ease, color 160ms ease;
        }

        .more-menu-trigger.is-active,
        .more-menu-trigger.is-open {
          color: #10233d;
          background: rgba(16, 35, 61, 0.08);
        }

        .menu-caret {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 18px;
          height: 18px;
          border-radius: 999px;
          background: rgba(16, 35, 61, 0.08);
          font-size: 0.95rem;
          line-height: 1;
        }

        .more-menu-panel {
          position: absolute;
          top: calc(100% + 10px);
          right: 0;
          min-width: 220px;
          padding: 10px;
          border: 1px solid rgba(142, 152, 166, 0.24);
          border-radius: 18px;
          background: rgba(255, 255, 255, 0.98);
          box-shadow: 0 18px 40px rgba(15, 23, 42, 0.12);
          display: grid;
          gap: 4px;
        }

        .menu-link {
          display: block;
          color: #4a5668;
          text-decoration: none;
          padding: 12px 14px;
          border-radius: 12px;
          font-size: 0.95rem;
          font-weight: 500;
        }

        .menu-link.is-active {
          color: #10233d;
          background: rgba(16, 35, 61, 0.08);
          font-weight: 600;
        }

        .desktop-account {
          display: flex;
          align-items: center;
          gap: 12px;
          padding-left: 10px;
          border-left: 1px solid rgba(142, 152, 166, 0.24);
        }

        .user-chip {
          display: inline-flex;
          align-items: center;
          gap: 10px;
          min-width: 0;
        }

        .user-avatar {
          border-radius: 999px;
          flex-shrink: 0;
          object-fit: cover;
          box-shadow: 0 6px 18px rgba(15, 23, 42, 0.12);
        }

        .user-avatar-fallback {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 36px;
          height: 36px;
          background: linear-gradient(135deg, #10233d, #375b82);
          color: #f8fafc;
          font-weight: 700;
        }

        .user-name {
          color: #4a5668;
          font-size: 0.92rem;
          font-weight: 500;
          max-width: 120px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .sign-out-link {
          color: #364152;
          text-decoration: none;
          font-size: 0.9rem;
          font-weight: 600;
          padding: 10px 14px;
          border-radius: 999px;
          border: 1px solid rgba(142, 152, 166, 0.34);
          background: rgba(255, 255, 255, 0.8);
          white-space: nowrap;
        }

        .mobile-menu-button {
          display: none;
          border: 0;
          border-radius: 999px;
          background: #10233d;
          color: #f8fafc;
          padding: 12px 16px;
          font-size: 0.95rem;
          font-weight: 600;
          letter-spacing: 0.01em;
          cursor: pointer;
          box-shadow: 0 12px 24px rgba(16, 35, 61, 0.18);
        }

        .mobile-menu-button.is-open {
          background: #223f62;
        }

        .mobile-menu-panel {
          margin-top: 14px;
          padding: 16px;
          border: 1px solid rgba(142, 152, 166, 0.24);
          border-radius: 24px;
          background:
            linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(245, 247, 250, 0.98)),
            #ffffff;
          box-shadow: 0 16px 34px rgba(15, 23, 42, 0.1);
        }

        .mobile-nav-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }

        .mobile-account-row {
          margin-top: 16px;
          padding-top: 16px;
          border-top: 1px solid rgba(142, 152, 166, 0.2);
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }

        .mobile-user-chip :global(img),
        .mobile-user-chip .user-avatar-fallback {
          width: 40px;
          height: 40px;
        }

        .mobile-sign-out-link {
          padding-left: 16px;
          padding-right: 16px;
        }

        @media (max-width: 900px) {
          .desktop-nav-group {
            display: none;
          }

          .mobile-menu-button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
          }

          .nav-inner {
            padding: 14px 16px;
          }
        }

        @media (max-width: 560px) {
          .mobile-nav-grid {
            grid-template-columns: minmax(0, 1fr);
          }

          .mobile-account-row {
            align-items: flex-start;
            flex-direction: column;
          }

          .brand-link {
            font-size: 1.2rem;
          }
        }
      `}</style>
    </header>
  );
}

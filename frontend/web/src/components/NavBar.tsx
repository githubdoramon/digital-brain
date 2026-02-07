import Link from "next/link";
import { getServerSession } from "next-auth/next";
import { authOptions } from "@/app/api/auth/[...nextauth]/route";
import Image from "next/image";

export async function NavBar() {
  const session = await getServerSession(authOptions);
    
  return (
    <header
      style={{
        borderBottom: "1px solid #e2e2e2",
        backgroundColor: "#fafafa",
      }}
    >
      <div
        style={{
          margin: "0 auto",
          maxWidth: "960px",
          padding: "16px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "16px",
        }}
      >
        <Link
          href="/"
          style={{
            fontSize: "1.25rem",
            fontWeight: 600,
            color: "#111",
          }}
        >
          Digital Brain
        </Link>
        <div style={{ display: "flex", alignItems: "center", gap: "24px" }}>
          <nav
            aria-label="Primary"
            style={{
              display: "flex",
              gap: "16px",
              fontSize: "0.95rem",
            }}
          >
            <Link
              href="/"
              style={{
                color: "#444",
              }}
            >
              Home
            </Link>
            <Link
              href="/contacts"
              style={{
                color: "#444",
              }}
            >
              Contacts
            </Link>
            <Link
              href="/documents"
              style={{
                color: "#444",
              }}
            >
              Documents
            </Link>
            <Link
              href="/todos"
              style={{
                color: "#444",
              }}
            >
              Todos
            </Link>
            <Link
              href="/meetings"
              style={{
                color: "#444",
              }}
            >
              Meetings
            </Link>
            <Link
              href="/system"
              style={{
                color: "#444",
              }}
            >
              System Status
            </Link>
            <Link
              href="/tools"
              style={{
                color: "#444",
              }}
            >
              Tools
            </Link>
          </nav>
          
          {session?.user && (
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                {session.user.image && (
                  <Image
                    src={session.user.image}
                    alt={session.user.name || "User"}
                    width={32}
                    height={32}
                    style={{ borderRadius: "50%" }}
                    unoptimized
                  />
                )}
                <span style={{ fontSize: "0.9rem", color: "#666" }}>
                  {session.user.name}
                </span>
              </div>
              <Link
                href="/api/auth/signout"
                style={{
                  padding: "6px 12px",
                  background: "#fff",
                  color: "#666",
                  border: "1px solid #d0d0d0",
                  borderRadius: "6px",
                  fontSize: "0.85rem",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                  textDecoration: "none",
                  display: "inline-block",
                }}
              >
                Sign Out
              </Link>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

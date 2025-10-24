import Link from "next/link";

export function NavBar() {
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
            href="/meetings"
            style={{
              color: "#444",
            }}
          >
            Meetings
          </Link>
        </nav>
      </div>
    </header>
  );
}

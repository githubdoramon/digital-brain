"use client";

import { Suspense } from "react";
import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";

export default function SignIn() {
  return (
    <Suspense fallback={<LoadingState />}>
      <SignInContent />
    </Suspense>
  );
}

function SignInContent() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") || "/";
  const error = searchParams.get("error");

  const handleSignIn = async () => {
    await signIn("google", { callbackUrl });
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "80vh",
        gap: "24px",
      }}
    >
      <div
        style={{
          background: "#fff",
          padding: "48px",
          borderRadius: "16px",
          boxShadow: "0 4px 12px rgba(15, 23, 42, 0.08)",
          textAlign: "center",
          maxWidth: "400px",
        }}
      >
        <h1 style={{ fontSize: "2rem", fontWeight: 600, marginBottom: "12px" }}>
          Welcome to Digital Brain
        </h1>
        <p style={{ color: "#666", marginBottom: "32px", lineHeight: 1.6 }}>
          Sign in with your Google account to access your personal memory orchestrator
          and AI-powered insights.
        </p>
        
        {error === "AccessDenied" && (
          <div
            style={{
              background: "#fee",
              color: "#c33",
              padding: "12px 16px",
              borderRadius: "8px",
              marginBottom: "24px",
              fontSize: "0.9rem",
              border: "1px solid #fcc",
            }}
          >
            ⚠️ Access denied. Your account is not authorized to access this application.
          </div>
        )}
        <button
          onClick={handleSignIn}
          style={{
            width: "100%",
            padding: "14px 24px",
            background: "#0b6bcb",
            color: "#fff",
            border: "none",
            borderRadius: "8px",
            fontSize: "1rem",
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "12px",
            transition: "background 0.2s",
          }}
          onMouseOver={(e) => (e.currentTarget.style.background = "#084a94")}
          onMouseOut={(e) => (e.currentTarget.style.background = "#0b6bcb")}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
          </svg>
          Sign in with Google
        </button>
      </div>
      <p style={{ color: "#999", fontSize: "0.875rem" }}>
        Your data is private and secure
      </p>
    </div>
  );
}

function LoadingState() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "80vh",
        color: "#666",
      }}
    >
      Loading sign-in...
    </div>
  );
}

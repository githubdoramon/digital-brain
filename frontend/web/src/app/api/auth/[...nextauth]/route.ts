import NextAuth, { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import { JWT } from "next-auth/jwt";

// Parse the allowlist from environment variable
const getAllowedUsers = (): Set<string> => {
  const allowlist = process.env.ALLOWED_USERS?.trim();
  if (!allowlist) {
    throw new Error("ALLOWED_USERS must be configured and non-empty");
  }
  const users = new Set(allowlist.split(",").map((u) => u.trim()).filter(Boolean));
  if (users.size === 0) {
    throw new Error("ALLOWED_USERS must contain at least one email address");
  }
  return users;
};

const allowedUsers = getAllowedUsers();

type GoogleJWT = JWT & {
  accessToken?: string;
  refreshToken?: string;
  accessTokenExpires?: number;
  idTokenExpires?: number;
  idToken?: string;
  error?: string;
};

type GoogleTokenResponse = {
  access_token?: string;
  expires_in?: number;
  refresh_token?: string;
  id_token?: string;
  error?: string;
};

type JwtPayload = {
  exp?: number;
};

function toFiniteNumber(value: unknown): number | undefined {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : undefined;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function getJwtExpiry(token: string | undefined): number | undefined {
  if (!token) return undefined;
  const parts = token.split(".");
  if (parts.length < 2) return undefined;

  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    const payload = JSON.parse(Buffer.from(padded, "base64").toString("utf8")) as JwtPayload;
    return payload.exp ? payload.exp * 1000 : undefined;
  } catch {
    return undefined;
  }
}

async function refreshGoogleToken(token: GoogleJWT): Promise<GoogleJWT> {
  if (!token.refreshToken) {
    return { ...token, error: "RefreshAccessTokenError" };
  }

  try {
    const response = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        client_id: process.env.GOOGLE_CLIENT_ID ?? "",
        client_secret: process.env.GOOGLE_CLIENT_SECRET ?? "",
        grant_type: "refresh_token",
        refresh_token: token.refreshToken,
      }),
    });

    const refreshedTokens = (await response.json()) as GoogleTokenResponse;

    if (!response.ok) {
      throw refreshedTokens;
    }

    return {
      ...token,
      accessToken: refreshedTokens.access_token ?? token.accessToken,
      accessTokenExpires: refreshedTokens.expires_in
        ? Date.now() + refreshedTokens.expires_in * 1000
        : token.accessTokenExpires,
      refreshToken: refreshedTokens.refresh_token ?? token.refreshToken,
      idToken: refreshedTokens.id_token ?? token.idToken,
      idTokenExpires: refreshedTokens.id_token
        ? getJwtExpiry(refreshedTokens.id_token)
        : token.idTokenExpires,
      error: undefined,
    };
  } catch (error) {
    console.error("Failed to refresh Google token", error);
    return {
      ...token,
      error: "RefreshAccessTokenError",
    };
  }
}

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: {
        params: {
          scope: "openid email profile",
          prompt: "consent",
          access_type: "offline",
          response_type: "code",
        },
      },
    }),
  ],
  pages: {
    signIn: "/auth/signin",
    error: "/auth/signin",
  },
  session: {
    strategy: "jwt",
    // Session will last for 365 days (effectively never expire during normal use)
    maxAge: 365 * 24 * 60 * 60, // 1 year in seconds
    // Update session age on every request to keep it fresh
    updateAge: 24 * 60 * 60, // Update every 24 hours
  },
  jwt: {
    maxAge: 365 * 24 * 60 * 60,
  },
  callbacks: {
    async signIn({ user }): Promise<boolean> {
      const userEmail = user.email;

      // Check if user is in allowlist
      if (userEmail && allowedUsers.has(userEmail)) {
        return true;
      }

      return false;
    },
    async session({ session, token }) {
      // Add the ID token to the session so frontend can use it
      if (token.idToken) {
        session.idToken = token.idToken as string;
      }
      if (token.error) {
        session.error = token.error as string;
      }
      return session;
    },
    async jwt({ token, user, account }) {
      // Store the Google ID token when user first signs in
      if (account?.id_token) {
        token.idToken = account.id_token;
        token.idTokenExpires = getJwtExpiry(account.id_token);
      }
      if (user) {
        token.id = user.id;
      }

      if (account?.access_token) {
        token.accessToken = account.access_token;
      }
      if (account?.refresh_token) {
        token.refreshToken = account.refresh_token;
      }
      if (account?.expires_at) {
        token.accessTokenExpires = account.expires_at * 1000;
      } else if (account?.expires_in) {
        const expiresInSeconds = Number(account.expires_in);
        if (Number.isFinite(expiresInSeconds)) {
          token.accessTokenExpires = Date.now() + expiresInSeconds * 1000;
        }
      }

      const bufferTime = 60 * 1000; // 1 minute
      const accessTokenExpiresMs = toFiniteNumber(token.accessTokenExpires);
      const idTokenExpiresMs = toFiniteNumber(token.idTokenExpires);
      const accessTokenFresh =
        accessTokenExpiresMs ? Date.now() < accessTokenExpiresMs - bufferTime : false;
      const idTokenFresh = idTokenExpiresMs ? Date.now() < idTokenExpiresMs - bufferTime : false;

      if (accessTokenFresh && idTokenFresh) {
        return token;
      }

      return refreshGoogleToken(token as GoogleJWT);
    },
  },
  secret: process.env.NEXTAUTH_SECRET,
};

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };

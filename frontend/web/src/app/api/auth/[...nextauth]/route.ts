import NextAuth, { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";

// Parse the allowlist from environment variable
const getAllowedUsers = (): Set<string> | null => {
  const allowlist = process.env.ALLOWED_USERS?.trim();
  if (!allowlist) return null;
  return new Set(allowlist.split(",").map(u => u.trim()).filter(Boolean));
};

const allowedUsers = getAllowedUsers();

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: {
        params: {
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
  callbacks: {
    async signIn({ user }): Promise<boolean> {
      const userEmail = user.email;
        
      // Check if user is in allowlist
      if (userEmail && allowedUsers && allowedUsers.has(userEmail)) {
        return true;
      }
      
      return false;
    },
    async session({ session, token }) {
      // Add the ID token to the session so frontend can use it
      if (token.idToken) {
        session.idToken = token.idToken as string;
      }
      return session;
    },
    async jwt({ token, user, account }) {
      // Store the Google ID token when user first signs in
      if (account?.id_token) {
        token.idToken = account.id_token;
      }
      if (user) {
        token.id = user.id;
      }
      return token;
    },
  },
  secret: process.env.NEXTAUTH_SECRET,
};

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };

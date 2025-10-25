import { withAuth } from "next-auth/middleware";

export default withAuth({
  callbacks: {
    authorized({ token }) {
      return !!token;
    },
  },
  pages: {
    signIn: "/auth/signin",
  },
});

export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico|auth/signin).*)"],
};

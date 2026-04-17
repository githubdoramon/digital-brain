import { getServerSession } from "next-auth/next";
import { authOptions } from "@/app/api/auth/[...nextauth]/route";
import { NavBarClient } from "@/components/NavBarClient";

export async function NavBar() {
  const session = await getServerSession(authOptions);

  return (
    <NavBarClient
      user={
        session?.user
          ? {
              name: session.user.name ?? null,
              image: session.user.image ?? null,
            }
          : null
      }
    />
  );
}

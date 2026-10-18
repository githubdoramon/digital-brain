import { redirect } from "next/navigation";

export default async function WhatsAppPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams({ provider: "whatsapp" });
  for (const key of ["view", "contact_id"]) {
    if (typeof params[key] === "string") query.set(key, params[key]);
  }
  redirect(`/system/messengers?${query}`);
}

import MessagingManager from "@/components/messaging/MessagingManager";

export default async function MessengersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const provider = params.provider === "telegram" ? "telegram" : "whatsapp";
  return <MessagingManager key={provider} provider={provider} />;
}

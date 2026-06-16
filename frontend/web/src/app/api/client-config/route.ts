export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json(
    {
      googleMapsApiKey: process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? "",
    },
    {
      headers: {
        "cache-control": "no-store",
      },
    }
  );
}

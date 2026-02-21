export type Place = {
  place_id: string;
  name: string | null;
  aliases: string[];
  address: string | null;
  city: string | null;
  country: string | null;
  lat: number | null;
  lon: number | null;
  role?: string | null;
};

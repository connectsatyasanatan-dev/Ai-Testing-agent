/**
 * useHealth — query hook for the /health endpoint.
 * Useful as a connectivity check or for displaying API version.
 */
import { useQuery } from "@tanstack/react-query";
import { getHealth, type HealthResponse } from "@/lib/api-client";

export function useHealth() {
  return useQuery<HealthResponse, Error>({
    queryKey: ["health"],
    queryFn: getHealth,
    staleTime: 60_000,  // health rarely changes
    retry: false,
  });
}

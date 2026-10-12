/**
 * TanStack Query client singleton.
 * - staleTime 30s: avoid redundant refetches for data just fetched.
 * - retry: do not retry on 4xx (auth failures, validation errors, policy blocks).
 */
import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status < 500) return false;
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getCurrentUser,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  type AuthUser,
} from "@/lib/api-client";

export const AUTH_QUERY_KEY = ["auth", "me"];

export function useAuth() {
  const queryClient = useQueryClient();

  const { data: user, isLoading, error } = useQuery<AuthUser | null>({
    queryKey: AUTH_QUERY_KEY,
    queryFn: getCurrentUser,
    staleTime: 60_000,
    retry: false,
  });

  const loginMutation = useMutation({
    mutationFn: apiLogin,
    onSuccess: (data) => {
      queryClient.setQueryData(AUTH_QUERY_KEY, data);
      void queryClient.invalidateQueries();
    },
  });

  const registerMutation = useMutation({
    mutationFn: apiRegister,
    onSuccess: (data) => {
      queryClient.setQueryData(AUTH_QUERY_KEY, data);
      void queryClient.invalidateQueries();
    },
  });

  const logoutMutation = useMutation({
    mutationFn: apiLogout,
    onSuccess: () => {
      queryClient.setQueryData(AUTH_QUERY_KEY, null);
      void queryClient.invalidateQueries();
    },
  });

  return {
    user: user ?? null,
    isAuthenticated: Boolean(user),
    isLoading,
    error,
    login: loginMutation.mutateAsync,
    isLoggingIn: loginMutation.isPending,
    loginError: loginMutation.error,
    register: registerMutation.mutateAsync,
    isRegistering: registerMutation.isPending,
    registerError: registerMutation.error,
    logout: logoutMutation.mutateAsync,
    isLoggingOut: logoutMutation.isPending,
  };
}

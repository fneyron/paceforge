interface StravaProfile {
  id: number;
  firstname: string;
  lastname: string;
  profile: string;
  profile_medium: string;
  city: string;
  state: string;
  country: string;
  sex: string;
  weight: number;
  ftp: number;
}

export function StravaProvider(config: {
  clientId: string;
  clientSecret: string;
}) {
  return {
    id: "strava" as const,
    name: "Strava",
    type: "oauth" as const,
    clientId: config.clientId,
    clientSecret: config.clientSecret,
    authorization: {
      url: "https://www.strava.com/oauth/authorize",
      params: {
        scope: "read,activity:read_all,profile:read_all",
        response_type: "code",
        approval_prompt: "auto",
      },
    },
    token: {
      url: "https://www.strava.com/oauth/token",
      async conform(response: Response) {
        // Strava returns a non-standard token response that includes
        // the athlete profile alongside the token fields.
        // We need to ensure the response is treated as valid.
        if (response.ok) {
          return response;
        }
        return response;
      },
    },
    userinfo: "https://www.strava.com/api/v3/athlete",
    // Strava sends client credentials in the POST body, not as Basic Auth
    client: {
      token_endpoint_auth_method: "client_secret_post" as const,
    },
    profile(profile: StravaProfile) {
      return {
        id: String(profile.id),
        name: `${profile.firstname} ${profile.lastname}`,
        image: profile.profile_medium || profile.profile,
      };
    },
    checks: ["state"] as ("state" | "pkce" | "none")[],
  };
}

import { RUNTIMES, type RuntimeName } from "./data";

export type Route =
  | { page: "results" }
  | { page: "seed"; seed: number; runtime: RuntimeName }
  | { page: "missing"; hash: string };

/** "#/" or "" is the results page; "#/seed/12/keyed" is a seed page. */
export function parseRoute(hash: string): Route {
  if (hash === "" || hash === "#" || hash === "#/") return { page: "results" };
  const match = /^#\/seed\/(\d+)\/([a-z]+)$/.exec(hash);
  const runtime = match?.[2] as RuntimeName | undefined;
  if (match && runtime && RUNTIMES.includes(runtime)) {
    return { page: "seed", seed: Number(match[1]), runtime };
  }
  return { page: "missing", hash };
}

export function seedHref(seed: number, runtime: RuntimeName): string {
  return `#/seed/${seed}/${runtime}`;
}

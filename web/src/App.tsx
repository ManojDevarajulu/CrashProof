import { useEffect, useState } from "react";
import { DataError, parseIndex, type SiteIndex } from "./data";
import { Results } from "./Results";
import { parseRoute, type Route } from "./route";
import { SeedPage } from "./Seed";
import { MobileSeedPicker, Sidebar } from "./Sidebar";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; index: SiteIndex };

function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const update = () => {
      setRoute(parseRoute(window.location.hash));
      document.querySelector(".content")?.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}

function Page({ index, route }: { index: SiteIndex; route: Route }) {
  if (route.page === "seed") return <SeedPage index={index} seed={route.seed} runtime={route.runtime} />;
  if (route.page === "missing") {
    return (
      <p role="alert" className="notice">
        Nothing lives at {route.hash}. Open the <a href="#/">overview</a> or pick a trace.
      </p>
    );
  }
  return <Results index={index} />;
}

function RunFacts({ index }: { index: SiteIndex }) {
  const facts: [string, string][] = [
    ["Seeds", String(index.summary.seeds)],
    ["Agent", "scripted"],
    ["Runtimes", "naive, keyed, hardened"],
    ["Invariants", "I1 to I9"],
  ];
  return (
    <dl className="facts">
      {facts.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function App() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const route = useRoute();

  useEffect(() => {
    fetch("data/index.json")
      .then((response) => {
        if (!response.ok) throw new DataError(`data/index.json: HTTP ${response.status}`);
        return response.json();
      })
      .then((raw: unknown) => setLoad({ state: "ready", index: parseIndex(raw) }))
      .catch((error: unknown) => setLoad({ state: "error", message: String(error) }));
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          Crashproof
        </a>
        {load.state === "ready" && <RunFacts index={load.index} />}
        {load.state === "ready" && <MobileSeedPicker index={load.index} route={route} />}
      </header>
      {load.state === "ready" && <Sidebar index={load.index} route={route} />}
      <main className="content">
        {load.state === "loading" && <p className="muted">Loading run results…</p>}
        {load.state === "error" && (
          <p role="alert" className="notice">
            Could not load the run results: {load.message}. Run <code>make eval</code>, then{" "}
            <code>make site</code>, then rebuild.
          </p>
        )}
        {load.state === "ready" && <Page index={load.index} route={route} />}
        <p className="note">
          Simulation only: fictional carriers and a scripted agent. Every trace replays byte for byte from its seed.
        </p>
      </main>
    </div>
  );
}

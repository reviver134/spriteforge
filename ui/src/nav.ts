// The studio's place lives in the URL (?project=…&agent=…) so reloads and the back button work
// without a router library or server-side routes.
import { useEffect, useState } from "react";

export interface Place {
  project: string | null;
  agent: string;
}

function read(): Place {
  const q = new URLSearchParams(window.location.search);
  return { project: q.get("project"), agent: q.get("agent") || "Director" };
}

export function href(place: Partial<Place>): string {
  const q = new URLSearchParams();
  if (place.project) q.set("project", place.project);
  if (place.project && place.agent && place.agent !== "Director") q.set("agent", place.agent);
  const s = q.toString();
  return s ? `/?${s}` : "/";
}

export function go(place: Partial<Place>) {
  window.history.pushState(null, "", href(place));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function usePlace(): Place {
  const [place, setPlace] = useState(read);
  useEffect(() => {
    const update = () => setPlace(read());
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return place;
}

/** A link that navigates inside the studio (and still opens in a new tab on middle-click). */
export function navClick(place: Partial<Place>) {
  return (e: React.MouseEvent) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    go(place);
  };
}

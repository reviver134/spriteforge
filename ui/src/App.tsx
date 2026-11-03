import { Home } from "./Home";
import { usePlace } from "./nav";
import { Studio } from "./Studio";

export function App() {
  const place = usePlace();
  return place.project ? <Studio runId={place.project} agent={place.agent} /> : <Home />;
}

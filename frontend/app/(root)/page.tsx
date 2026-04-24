import { redirect } from "next/navigation";

// The app's "homepage" is Risk Levels — hitting "/" forwards there so the
// URL bar reflects the route the user is actually viewing and the sidebar
// highlights the right item.
export default function Home() {
  redirect("/risk-levels");
}

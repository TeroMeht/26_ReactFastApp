// types/portfolio.ts
import { paths } from "@/generated/api";

export type OpenPosition =
  paths["/api/portfolio/open-risk-table"]["get"]["responses"]["200"]["content"]["application/json"][number];
import { linkVariants } from "@heroui/react";

export type CardState = "loading" | "error" | "empty" | "data";

/** HeroUI link styles for react-router links (keeps the /app/ basename). */
export const overviewLinkClass = linkVariants().base();

interface QueryLike<T> {
  isPending: boolean;
  isError: boolean;
  data: T | undefined;
}

/** A card's state from one query. */
export function queryCardState<T>(query: QueryLike<T>, isEmpty: (data: T) => boolean): CardState {
  if (query.isError) return "error";
  if (query.isPending || query.data === undefined) return "loading";
  return isEmpty(query.data) ? "empty" : "data";
}

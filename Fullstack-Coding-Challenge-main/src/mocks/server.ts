import { setupServer } from "msw/node";
import { handlers } from "@/mocks/handlers";

// Server-side MSW instance for use in tests
export const server = setupServer(...handlers);

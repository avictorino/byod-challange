import { render, screen } from "@testing-library/react";
import { MockedProvider } from "@apollo/client/testing";
import { describe, it, expect } from "vitest";
import { GET_CARS } from "@/graphql/queries";
import Example from "@/components/Example";

const mockCars = [
  {
    id: "1",
    make: "Toyota",
    model: "Camry",
    year: 2024,
    color: "Silver",
    mobile: "https://placehold.co/640x360",
    tablet: "https://placehold.co/1023x576",
    desktop: "https://placehold.co/1440x810",
  },
];

const mockCarsWithTypename = mockCars.map((car) => ({
  ...car,
  __typename: "Car" as const,
}));

const mocks = [
  {
    request: { query: GET_CARS },
    result: { data: { cars: mockCarsWithTypename } },
  },
];

describe("Example component", () => {
  it("renders car data from GraphQL", async () => {
    render(
      <MockedProvider mocks={mocks}>
        <Example />
      </MockedProvider>
    );

    expect(await screen.findByText("2024 Toyota Camry")).toBeInTheDocument();
    expect(screen.getByText("Silver")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    render(
      <MockedProvider mocks={mocks}>
        <Example />
      </MockedProvider>
    );

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });
});

import { graphql, HttpResponse } from "msw";
import { seedCars } from "@/mocks/data";
import type { Car } from "@/types";

// In-memory store so mutations persist during the session
let cars: Car[] = [...seedCars];
let nextId = cars.length + 1;

export const handlers = [
  // GetCars query — returns all cars
  graphql.query("GetCars", () => {
    return HttpResponse.json({
      data: { cars },
    });
  }),

  // GetCar query — fetch a single car by ID
  graphql.query("GetCar", ({ variables }) => {
    const car = cars.find((c) => c.id === variables["id"]);
    if (!car) {
      return HttpResponse.json({
        data: { car: null },
        errors: [{ message: `Car with id ${variables["id"]} not found` }],
      });
    }
    return HttpResponse.json({
      data: { car },
    });
  }),

  // AddCar mutation — adds a car and returns it
  graphql.mutation("AddCar", ({ variables }) => {
    const newCar: Car = {
      id: String(nextId++),
      make: variables["make"] as string,
      model: variables["model"] as string,
      year: variables["year"] as number,
      color: variables["color"] as string,
      mobile: `https://placehold.co/640x360?text=${encodeURIComponent(`${variables["make"]} ${variables["model"]}`)}+Mobile`,
      tablet: `https://placehold.co/1023x576?text=${encodeURIComponent(`${variables["make"]} ${variables["model"]}`)}+Tablet`,
      desktop: `https://placehold.co/1440x810?text=${encodeURIComponent(`${variables["make"]} ${variables["model"]}`)}+Desktop`,
    };
    cars = [...cars, newCar];

    return HttpResponse.json({
      data: { addCar: newCar },
    });
  }),
];

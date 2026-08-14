import { useQuery } from "@apollo/client";
import {
  Card,
  CardContent,
  Typography,
  CircularProgress,
  Alert,
} from "@mui/material";
import { GET_CARS } from "@/graphql/queries";
import type { Car } from "@/types";

/**
 * Example component demonstrating how to use Apollo + MUI together.
 * This is provided as a reference — feel free to delete or replace it.
 */
export default function Example() {
  const { data, loading, error } = useQuery<{ cars: Car[] }>(GET_CARS);

  if (loading) return <CircularProgress />;
  if (error) return <Alert severity="error">{error.message}</Alert>;

  return (
    <>
      {data?.cars.map((car) => (
        <Card key={car.id} sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6">
              {car.year} {car.make} {car.model}
            </Typography>
            <Typography color="text.secondary">{car.color}</Typography>
          </CardContent>
        </Card>
      ))}
    </>
  );
}

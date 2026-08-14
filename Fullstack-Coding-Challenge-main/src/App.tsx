import { Container, Typography, Box } from "@mui/material";

export default function App() {
  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Typography variant="h3" component="h1" gutterBottom>
        Car Inventory Manager
      </Typography>
      <Box>
        <Typography color="text.secondary">
          Replace this with your generated components. The GraphQL API, Apollo
          Client, and MSW are already configured and ready to use.
        </Typography>
      </Box>
    </Container>
  );
}

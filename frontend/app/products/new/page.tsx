import ProductForm from "@/components/ProductForm";
import { PageHeader } from "@/components/ui";

export default function NewProductPage() {
  return (
    <div>
      <PageHeader title="New product" subtitle="Saved as a YAML profile on the server." />
      <ProductForm />
    </div>
  );
}

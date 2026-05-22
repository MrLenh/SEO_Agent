from typing import Optional
from pydantic import BaseModel, Field


class PublishToShopifyRequest(BaseModel):
    blog_id: int = Field(..., description="Shopify blog ID (channel platform_id)")
    author: str = "SEO Agent"
    published: bool = True   # False → saves as draft on Shopify side


class GenerateImageRequest(BaseModel):
    prompt: Optional[str] = Field(None, description="Override stored image_prompt")
    size: str = Field("1792x1024", description="1024x1024 | 1792x1024 | 1024x1792")


class FullPublishRequest(BaseModel):
    blog_id: int = Field(..., description="Shopify blog ID (channel platform_id)")
    author: str = "SEO Agent"
    published: bool = True
    generate_image: bool = True
    image_prompt: Optional[str] = Field(None, description="Override stored image_prompt")
    image_size: str = "1792x1024"

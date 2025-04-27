# lost-and-found-app
An AI Based Lost and Found System

# FoundIt AI - Build-A-Thon Submission 🚀

[![Hackathon Badge](https://img.shields.io/badge/Hackathon-Build--A--Thon-blueviolet)](<Your Hackathon Link - Optional>)

**FoundIt AI** is an intelligent web-based platform designed to reunite lost items with their owners using the power of AI image analysis. Built for the **Build-A-Thon** hackathon, this platform leverages the Google Gemini 1.5 Flash API to analyze images, generate descriptions, and intelligently match lost and found items.

![FoundIt AI Concept Image](<Optional: Link to a screenshot or logo>)

---

## 🌟 Core Problem & Solution

Losing personal belongings is stressful. Traditional lost and found methods (physical bins, basic online forums) are often inefficient. FoundIt AI aims to significantly improve the process by:

1.  **Automating Item Description:** Using AI to generate objective descriptions from images, reducing reliance on subjective user input.
2.  **Intelligent Matching:** Employing a multi-tiered approach combining text semantics, metadata, and visual similarity for more accurate matches.
3.  **Streamlining Connection:** Providing a centralized platform for finders and losers to connect more easily.

---

## ✨ Key Features

*   **Report Found Items:**
    *   Upload a photo of the found item.
    *   🤖 **AI Analysis:** Gemini 1.5 Flash generates a detailed description.
    *   Provide additional details (Type, Color, Brand, Location Found).
    *   Securely store item information and (privacy-controlled) contact details.
*   **Report Lost Items:**
    *   Upload a photo of the lost item (or a similar reference image).
    *   🤖 **AI Analysis:** Gemini 1.5 Flash generates a description.
    *   Provide details (Type, Color, Brand, Last Known Location).
    *   Securely store item information and contact details.
*   **🧠 AI-Powered Matching Algorithm:** A three-tiered system identifies potential matches:
    1.  **Description Matching (≥40%):** Compares AI-generated descriptions using Gemini for semantic similarity.
    2.  **Metadata Matching (≥50%):** Compares structured data (Type, Color, Brand, Location Proximity).
    3.  **Image Comparison (≥80%):** Compares actual images using Gemini 1.5 Flash for visual similarity analysis on items passing the first two tiers.
*   **View Potential Matches:**
    *   Displays potential matches for lost items.
    *   Includes found item image, details, and match confidence score.
    *   Provides finder's contact information (with privacy controls).
*   **User-Friendly Interface:** Simple forms for reporting and clear display of results.

---

## ⚙️ How It Works - The AI Magic

The core intelligence lies in the integration with the **Google Gemini 1.5 Flash API**:

1.  **Image -> Description:** When an image is uploaded (lost or found), it's sent to the Gemini API with a prompt asking for a detailed description relevant for identification.
    *   *Model Used:* `gemini-1.5-flash-latest`
    *   *API Key Group:* Text/Other (`AIzaSyAGgQ...`)
2.  **Description Similarity:** To compare two items based on their AI-generated descriptions, the descriptions are sent back to the Gemini API with a prompt asking it to calculate their semantic similarity score (0-100).
    *   *Model Used:* `gemini-1.5-flash-latest`
    *   *API Key Group:* Text/Other (`AIzaSyAGgQ...`)
3.  **Image Similarity:** For items that pass the description and metadata checks, their actual images are sent to the Gemini API (as multimodal input) with a prompt asking it to assess the visual similarity and likelihood of being the *same* object, returning a score (0-100).
    *   *Model Used:* `gemini-1.5-flash-latest`
    *   *API Key Group:* Image Comparison (`AIzaSyDyfU...`)

This tiered approach ensures efficiency (computationally cheaper text/metadata checks first) and accuracy (detailed visual check for promising candidates).

---

## 🚶 Example User Flow (Lost Watch)

1.  **User Loses Watch:** User realizes their Timex watch is missing, last seen in Central Park.
2.  **Report Lost Item:**
    *   User navigates to the "Report Lost Item" page on FoundIt AI.
    *   Uploads a photo of an identical/similar Timex watch.
    *   🤖 AI generates: "Silver analog wristwatch with brown leather band, circular face."
    *   User adds/confirms details: Type: `Watch`, Color: `Silver`, Brand: `Timex`, Location: `Central Park`, Contact: `user@email.com`.
    *   Information is saved to the database.
3.  **Matching Process:** The system automatically compares the new lost item report against the database of found items using the three-tiered algorithm.
4.  **Potential Match Found:** Another user previously found a watch matching the criteria:
    *   Description Similarity: 75% (>40%)
    *   Metadata Similarity: (Type=Match, Color=Match, Brand=Match, Location=Nearby) -> 80% (>50%)
    *   Image Similarity: 92% (>80%)
5.  **Results Display:** The user who lost the watch sees the found watch as a potential match, including its image, details, confidence score (e.g., 85%), and a button to reveal the finder's contact information.

---

## 🛠️ Technology Stack

*   **Frontend:** [Specify: e.g., React, Vue, HTML/CSS/JS]
*   **Backend:** [Specify: e.g., Node.js/Express, Python/Flask]
*   **Database:** [Specify: e.g., PostgreSQL, MySQL, MongoDB]
*   **AI:** Google Gemini 1.5 Flash API (`gemini-1.5-flash-latest`)
*   **Image Storage:** [Specify: e.g., Google Cloud Storage, AWS S3, Cloudinary]
*   **Deployment:** [Specify where it's hosted, e.g., Google Cloud Run, Vercel, Heroku, Local]

---

## 🔑 API Keys & Configuration

**IMPORTANT:** This project requires API keys for the Google Gemini API.

*   The specific keys used during the hackathon were provided in the prompt:
    *   Image Comparison Key: `AIzaSyDyfUFysP_nUUmUPFHwq9APuzLsT04qeVY` (Example Only)
    *   Text/Other Key: `AIzaSyAGgQ12y7ytWNUqjWdjnm6Va1iJFpb5f4c` (Example Only)
*   **DO NOT COMMIT YOUR ACTUAL API KEYS TO GITHUB.**
*   Store your keys securely using environment variables. Create a `.env` file in the root of the backend directory (and add `.env` to your `.gitignore` file):

```bash
# .env file format

# For Gemini API interactions (text generation, description similarity)
GEMINI_API_KEY_TEXT_OTHER=<Your_Text_Other_API_Key>

# For Gemini API interactions (image similarity comparison)
GEMINI_API_KEY_IMAGE_COMPARISON=<Your_Image_Comparison_API_Key>

# Database Connection String/Credentials
DATABASE_URL=<Your_Database_Connection_String>

# Cloud Storage Credentials (if applicable)
GCS_BUCKET_NAME=<Your_Bucket_Name>
GCS_PROJECT_ID=<Your_Project_ID>
# Add other necessary credentials as needed

package com.laxman.xrayluggage.model;

import java.time.LocalDateTime;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Data;
import lombok.NoArgsConstructor;

// @Entity tells JPA this class maps to a database table
// Replaces: class Detection(Base) in database.py
@Entity
@Table(name = "detections")
@Data               // Lombok: auto-generates getters, setters, toString
@NoArgsConstructor  // Lombok: auto-generates empty constructor (required by JPA)
public class Detection {

    // Replaces: id = Column(Integer, primary_key=True, index=True)
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY) // auto-increment
    private Long id;

    // Replaces: timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    @Column(nullable = false)
    private LocalDateTime timestamp = LocalDateTime.now();

    // Replaces: date_dmy = Column(String(20))
    @Column(name = "date_dmy", length = 20)
    private String dateDmy;

    // Replaces: time_hms = Column(String(20))
    @Column(name = "time_hms", length = 20)
    private String timeHms;

    // Replaces: class_name = Column(String(100), index=True)
    @Column(name = "class_name", length = 100)
    private String className;

    // Replaces: filename = Column(String(255))
    @Column(length = 255)
    private String filename;

    // Replaces: image_url = Column(Text, nullable=True)  # Cloudinary URL
    @Column(name = "image_url", columnDefinition = "TEXT")
    private String imageUrl;

    // Replaces: conf = Column(Float, nullable=True)
    private Double conf;

    // Replaces: camera_idx = Column(Integer, default=0)
    @Column(name = "camera_idx")
    private Integer cameraIdx = 0;
}
 